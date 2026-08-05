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


def test_mask_hides_middle():
    assert mask("abcdefghijklmnop") == "abc...mnop"


def test_mask_short_value_is_fully_hidden():
    """У 8-символьного значения «три первых плюс четыре последних» скрывает
    ровно один символ — маскировать так нельзя."""
    assert mask("short") == "***"


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


def test_service_defaults(db_session):
    service = SettingsService(db_session, KEY)
    assert service.get_str("absent", "default") == "default"
    assert service.get_int("absent", 4) == 4
    assert service.get_bool("absent", True) is True


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
