def test_manager_cannot_read_settings(manager_client):
    assert manager_client.get("/api/admin/settings").status_code == 403


def test_admin_reads_defaults(admin_client):
    resp = admin_client.get("/api/admin/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["image_model"] == "openai/gpt-image-2"
    assert body["image_quality"] == "medium"
    assert body["routerai_base_url"] == "https://routerai.ru/api/v1"


def test_secret_is_never_returned_raw(admin_client):
    admin_client.put("/api/admin/settings", json={"routerai_api_key": "sk-super-secret-value"})
    body = admin_client.get("/api/admin/settings").json()
    assert body["routerai_api_key"] == "sk-...alue"


def test_empty_secret_means_keep_current(admin_client, db_session):
    from app.config import config
    from app.settings.service import SettingsService

    admin_client.put("/api/admin/settings", json={"routerai_api_key": "sk-super-secret-value"})
    admin_client.put("/api/admin/settings", json={"routerai_api_key": "", "image_quality": "high"})

    service = SettingsService(db_session, config.encryption_key)
    assert service.get_secret("routerai_api_key") == "sk-super-secret-value"
    assert service.get_str("image_quality") == "high"


def test_update_plain_value(admin_client):
    admin_client.put("/api/admin/settings", json={"image_workers": "6"})
    assert admin_client.get("/api/admin/settings").json()["image_workers"] == "6"


def test_invalid_int_setting_rejected(admin_client):
    """image_workers валидируется на PUT, до записи — иначе опечатка проходит
    с 200 и падает позже необработанным ValueError внутри celery-таски
    (Task 8), где её уже никто не увидит."""
    resp = admin_client.put("/api/admin/settings", json={"image_workers": ""})
    assert resp.status_code == 422


def test_secret_decryption_error_returns_empty_value_and_errors_key(admin_client, db_session):
    """Если ENCRYPTION_KEY сменили после того, как секрет сохранён старым
    ключом, get_secret бросает SecretDecryptionError. Поле секрета обязано
    остаться пустым (безопасно для round-trip в PUT — пустая строка значит
    «не менять»), а диагностика уходит в отдельный ключ _errors, откуда её
    точно не отправят обратно как значение."""
    from cryptography.fernet import Fernet

    from app.config import config

    admin_client.put("/api/admin/settings", json={"routerai_api_key": "sk-super-secret-value"})

    original_key = config.encryption_key
    config.encryption_key = Fernet.generate_key().decode()
    try:
        body = admin_client.get("/api/admin/settings").json()
    finally:
        config.encryption_key = original_key

    assert body["routerai_api_key"] == ""
    assert "routerai_api_key" in body["_errors"]


def test_get_response_echoed_back_does_not_overwrite_secret(admin_client, db_session):
    """Регрессия на найденный сценарий потери данных: раньше GET при чужом
    ENCRYPTION_KEY клал в поле секрета текст ошибки («ОШИБКА: ...»), и если
    фронт отправлял ответ GET целиком обратно в PUT (обычный паттерн «сохранить
    всю форму»), этот текст шифровался поверх настоящего ключа — и уже
    ничем не восстанавливался, даже возвратом правильного ENCRYPTION_KEY."""
    from cryptography.fernet import Fernet

    from app.config import config
    from app.settings.service import SettingsService

    admin_client.put("/api/admin/settings", json={"routerai_api_key": "sk-super-secret-value"})

    original_key = config.encryption_key
    config.encryption_key = Fernet.generate_key().decode()
    try:
        body = admin_client.get("/api/admin/settings").json()
        admin_client.put("/api/admin/settings", json=body)
    finally:
        config.encryption_key = original_key

    service = SettingsService(db_session, config.encryption_key)
    assert service.get_secret("routerai_api_key") == "sk-super-secret-value"


def test_seed_settings_retries_once_on_concurrent_insert(db_session, monkeypatch):
    """seed_settings пишет через db.add напрямую, минуя защищённый от гонки
    SettingsService._upsert, и вызывается на каждом GET — тот же класс
    гонки, что чинили в Task 5, но здесь без защиты. Симулируем конкурента,
    вставившего и закоммитившего одну из дефолтных настроек первым (между
    нашим SELECT-проходом и commit()): наш commit() обязан поймать конфликт
    первичного ключа, откатиться и повторить один раз, а не отдать наружу
    голый IntegrityError (→ 500 у одного из двух админов, открывших
    страницу настроек одновременно на пустой БД)."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.models.setting import Setting
    from app.seed import DEFAULT_SETTINGS, seed_settings

    real_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            # Конкурент реально занимает строку в БД первым, прежде чем наш
            # собственный commit() (уже пытающийся вставить ту же строку)
            # получит отказ по PK.
            db_session.rollback()
            db_session.execute(
                text(
                    "INSERT INTO settings (key, value, is_secret) "
                    "VALUES ('text_model', 'concurrent-value', 0)"
                )
            )
            real_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)

    seed_settings(db_session)

    assert calls["n"] == 2
    # Конкурентно вставленное значение не перезаписывается — идемпотентность
    # seed_settings сохраняется и на повторе после гонки.
    assert db_session.get(Setting, "text_model").value == "concurrent-value"
    for key, value in DEFAULT_SETTINGS.items():
        if key == "text_model":
            continue
        assert db_session.get(Setting, key).value == value


def test_put_does_not_call_seed_settings_again(admin_client, monkeypatch):
    """PUT собирает ответ через отдельную от GET функцию и не должен зависеть
    от повторного наполнения дефолтов (со своим отдельным commit() внутри
    seed_settings) — иначе сбой в этом наполнении превращает уже сохранённые
    изменения в ложный 500 у клиента, хотя сам payload уже записан."""
    import app.api.admin_settings as admin_settings_module

    def boom(db):
        raise RuntimeError("seed_settings не должен вызываться из PUT")

    monkeypatch.setattr(admin_settings_module, "seed_settings", boom)

    resp = admin_client.put("/api/admin/settings", json={"image_workers": "6"})
    assert resp.status_code == 200
    assert resp.json()["image_workers"] == "6"
