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
