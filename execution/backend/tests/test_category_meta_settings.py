def test_wordstat_settings_have_defaults(admin_client):
    body = admin_client.get("/api/admin/settings").json()
    assert body["wordstat_hourly_limit"] == "100"
    assert "леруа" in body["meta_stoplist"]
    assert body["wordstat_api_key"] == ""


def test_wordstat_key_is_secret(admin_client):
    admin_client.put("/api/admin/settings", json={"wordstat_api_key": "AQVN-real-secret-key"})
    shown = admin_client.get("/api/admin/settings").json()["wordstat_api_key"]
    assert shown and shown != "AQVN-real-secret-key"


def test_wordstat_hourly_limit_is_validated(admin_client):
    resp = admin_client.put("/api/admin/settings", json={"wordstat_hourly_limit": "0"})
    assert resp.status_code == 422
