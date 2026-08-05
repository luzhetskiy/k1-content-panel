import pytest
from cryptography.fernet import Fernet

from app.settings.crypto import SecretDecryptionError, decrypt, encrypt, mask
from app.settings.service import SettingsService

KEY = Fernet.generate_key().decode()


def test_encrypt_roundtrip():
    assert decrypt(encrypt("token-123", KEY), KEY) == "token-123"


def test_encrypt_output_is_not_plaintext():
    assert "token-123" not in encrypt("token-123", KEY)


def test_decrypt_with_other_key_raises():
    other = Fernet.generate_key().decode()
    with pytest.raises(SecretDecryptionError):
        decrypt(encrypt("token-123", KEY), other)


def test_encrypt_rejects_malformed_key():
    """Обрезанный ключ или hex вместо base64 проходит проверку prod-compose
    (${ENCRYPTION_KEY:?...} ловит только пустую строку), но не Fernet —
    здесь это должно стать понятной ошибкой, а не голым ValueError."""
    with pytest.raises(SecretDecryptionError, match="невалиден"):
        encrypt("token-123", "not-a-valid-fernet-key")


def test_decrypt_rejects_malformed_key():
    ciphertext = encrypt("token-123", KEY)
    with pytest.raises(SecretDecryptionError, match="невалиден"):
        decrypt(ciphertext, "not-a-valid-fernet-key")


def test_decrypt_error_messages_distinguish_bad_key_from_wrong_key():
    """«Ключ невалиден по формату» и «значение зашифровано другим ключом» —
    разные причины и разные действия админа, сообщения не должны совпадать."""
    ciphertext = encrypt("token-123", KEY)
    other_key = Fernet.generate_key().decode()

    with pytest.raises(SecretDecryptionError, match="невалиден") as bad_format:
        decrypt(ciphertext, "not-a-valid-fernet-key")
    with pytest.raises(SecretDecryptionError, match="другим ключом") as wrong_key:
        decrypt(ciphertext, other_key)

    assert str(bad_format.value) != str(wrong_key.value)


def test_mask_hides_middle():
    assert mask("abcdefghijklmnop") == "abc...mnop"


def test_mask_hides_value_just_below_threshold():
    """Порог MIN_MASKABLE_LEN = 12. У 11-символьного значения формула «три
    первых плюс четыре последних» перекрывала бы 7 из 11 символов — почти
    ничего не скрыто, поэтому ниже порога значение прячется целиком."""
    assert mask("sk-abcdefgh") == "***"


def test_mask_shows_head_and_tail_at_threshold():
    """12 символов — ровно порог: голова и хвост уже не пересекаются."""
    assert mask("abcdefghijkl") == "abc...ijkl"


def test_mask_empty_value_is_fully_hidden():
    assert mask("") == "***"


def test_service_stores_plain_value(db_session):
    service = SettingsService(db_session, KEY)
    service.set("text_model", "anthropic/claude-sonnet-4-6")
    assert service.get_str("text_model") == "anthropic/claude-sonnet-4-6"


def test_service_stores_secret_encrypted(db_session):
    from app.models.setting import Setting

    service = SettingsService(db_session, KEY)
    service.set_secret("routerai_api_key", "sk-real-key")
    row = db_session.get(Setting, "routerai_api_key")
    assert row.is_secret is True
    assert "sk-real-key" not in row.value
    assert service.get_secret("routerai_api_key") == "sk-real-key"


def test_service_get_str_on_secret_key_raises(db_session):
    """is_secret не читается боевым кодом Task 6 (секретность там определяет
    отдельная константа SECRET_KEYS) — эта проверка защита от случайного
    get_str() по секретному ключу где-то ещё, который иначе тихо отдал бы
    шифротекст вида gAAAAABq... вызывающему."""
    service = SettingsService(db_session, KEY)
    service.set_secret("routerai_api_key", "sk-real-key")

    with pytest.raises(SecretDecryptionError):
        service.get_str("routerai_api_key")


def test_service_set_after_set_secret_clears_is_secret(db_session):
    """Переход секрет → обычное значение: set() обязан сбросить is_secret,
    иначе следующий get_str() на этот же ключ упадёт (см. проверку выше),
    хотя значение уже не секрет."""
    from app.models.setting import Setting

    service = SettingsService(db_session, KEY)
    service.set_secret("routerai_api_key", "sk-real-key")
    service.set("routerai_api_key", "not-a-secret-anymore")

    row = db_session.get(Setting, "routerai_api_key")
    assert row.is_secret is False
    assert service.get_str("routerai_api_key") == "not-a-secret-anymore"


def test_service_defaults(db_session):
    service = SettingsService(db_session, KEY)
    assert service.get_str("absent", "default") == "default"
    assert service.get_int("absent", 4) == 4
    assert service.get_bool("absent", True) is True


def test_service_get_secret_returns_default_when_never_set(db_session):
    """Путь, по которому Task 6 идёт при каждом GET настроек до того, как
    админ впервые введёт ключ: строки в settings ещё нет вовсе."""
    service = SettingsService(db_session, KEY)
    assert service.get_secret("routerai_api_key", "d") == "d"


def test_service_get_secret_with_other_key_raises(db_session):
    """Ветка в сервисе (не только в crypto.decrypt): при смене ENCRYPTION_KEY
    get_secret обязан упасть с понятным сообщением, а не молча вернуть
    default — иначе клиент RouterAI из Task 7 получит невнятный 401 вместо
    причины. Сообщение должно называть конкретную настройку — их два десятка."""
    service = SettingsService(db_session, KEY)
    service.set_secret("routerai_api_key", "sk-real-key")

    other_key = Fernet.generate_key().decode()
    other_service = SettingsService(db_session, other_key)

    with pytest.raises(SecretDecryptionError, match="routerai_api_key"):
        other_service.get_secret("routerai_api_key")


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("", False),
    ],
)
def test_service_get_bool_parses_stored_value(db_session, stored, expected):
    service = SettingsService(db_session, KEY)
    service.set("flag", stored)
    assert service.get_bool("flag", not expected) is expected


def test_service_get_int_parses_stored_value(db_session):
    service = SettingsService(db_session, KEY)
    service.set("max_articles_per_day", "42")
    assert service.get_int("max_articles_per_day", 0) == 42


def test_service_set_retries_once_on_concurrent_first_insert(db_session, monkeypatch):
    """Гонка на первой записи ключа: наша сессия не видит строку (SELECT —
    промах) и готовит INSERT, но кто-то другой успевает вставить и
    закоммитить строку с тем же key первым — Task 6 зовёт seed_settings на
    каждом GET, так что два админа на пустой БД сталкиваются здесь же.
    commit() должен словить конфликт первичного ключа и повторить операцию
    один раз как UPDATE, а не отдать вызывающему голый IntegrityError."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    real_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            # Симулируем конкурента: он вставляет и коммитит первым, реально
            # занимая строку в БД, прежде чем наш собственный commit() (уже
            # пытающийся вставить ту же строку) получит отказ по PK.
            db_session.rollback()
            db_session.execute(
                text(
                    "INSERT INTO settings (key, value, is_secret) "
                    "VALUES ('max_articles_per_day', '1', 0)"
                )
            )
            real_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)

    service = SettingsService(db_session, KEY)
    service.set("max_articles_per_day", "42")

    assert calls["n"] == 2
    assert service.get_str("max_articles_per_day") == "42"
