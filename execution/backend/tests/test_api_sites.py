import pytest


@pytest.fixture
def site_payload():
    return {
        "name": "Стройбаза Самара",
        "domain": "stroybaza-samara.ru",
        "base_url": "https://stroybaza-samara.ru",
        "api_token": "real-secret-token-value",
        "publish_target": "pages",
        "articles_parent_id": 25,
        "reference_article_id": 312,
        "site_description": "Строительная база в Самаре, аудитория — частные застройщики",
        "tone_of_voice": "практичный, без рекламных обещаний",
        "image_style_prompt": "реалистичное фото стройки",
        "cover_mode": "prompt",
        "cover_style_prompt": "широкая обложка",
        "teaser_category_id": 3,
        "teaser_city_id": 2,
        "teaser_location_id": 1,
    }


def test_manager_cannot_create_site(manager_client, site_payload):
    assert manager_client.post("/api/admin/sites", json=site_payload).status_code == 403


def test_admin_creates_site(admin_client, site_payload):
    resp = admin_client.post("/api/admin/sites", json=site_payload)
    assert resp.status_code == 200
    assert resp.json()["domain"] == "stroybaza-samara.ru"


def test_token_is_masked_in_response(admin_client, site_payload):
    created = admin_client.post("/api/admin/sites", json=site_payload).json()
    assert created["api_token"] == "rea...alue"


def test_token_is_stored_encrypted(admin_client, db_session, site_payload):
    admin_client.post("/api/admin/sites", json=site_payload)
    from app.models.site import Site

    site = db_session.query(Site).first()
    assert "real-secret-token-value" not in site.api_token_enc


def test_empty_token_on_update_keeps_current(admin_client, db_session, site_payload):
    from app.config import config
    from app.models.site import Site
    from app.settings.crypto import decrypt

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    admin_client.put(f"/api/admin/sites/{site_id}",
                     json={**site_payload, "api_token": "", "tone_of_voice": "сухой"})

    site = db_session.get(Site, site_id)
    assert decrypt(site.api_token_enc, config.encryption_key) == "real-secret-token-value"
    assert site.tone_of_voice == "сухой"


def test_duplicate_domain_rejected(admin_client, site_payload):
    admin_client.post("/api/admin/sites", json=site_payload)
    resp = admin_client.post("/api/admin/sites", json=site_payload)
    assert resp.status_code == 400
    assert "уже" in resp.json()["detail"]


def test_manager_sees_site_list_without_tokens(admin_client, manager_client, site_payload):
    admin_client.post("/api/admin/sites", json=site_payload)
    resp = manager_client.get("/api/sites")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "Стройбаза Самара"
    assert "api_token" not in body[0]
    assert "api_token_enc" not in body[0]


def test_inactive_site_hidden_from_manager_list(admin_client, manager_client, site_payload):
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    admin_client.put(f"/api/admin/sites/{site_id}",
                     json={**site_payload, "api_token": "", "is_active": False})
    assert manager_client.get("/api/sites").json() == []


def patch_site_api(monkeypatch, parent_url="/poleznye-stati/", reference_html="<img><img>"):
    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": parent_url}
        return {"id": page_id, "text": reference_html}

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.list_section_pages",
        lambda self, prefix: [{"id": 1, "title": "A", "url": prefix + "a/"}])


def test_sync_fills_prefix_images_and_page_count(admin_client, site_payload, monkeypatch):
    patch_site_api(monkeypatch, reference_html="<p>t</p><img><img><img>")
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body == {"ok": True, "url_prefix": "/poleznye-stati/", "pages": 1,
                    "reference_images": 3, "detail": ""}


def test_sync_result_is_persisted_on_the_site(admin_client, db_session, site_payload,
                                              monkeypatch):
    from app.models.site import Site

    patch_site_api(monkeypatch)
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    admin_client.post(f"/api/admin/sites/{site_id}/sync")

    site = db_session.get(Site, site_id)
    assert site.articles_url_prefix == "/poleznye-stati/"
    assert site.reference_images == 2
    assert site.reference_synced_at is not None


def test_sync_reports_api_failure_without_raising(admin_client, site_payload, monkeypatch):
    from app.sites.client import SiteAPIError

    def boom(self, page_id):
        raise SiteAPIError("страница 25: HTTP 403: Forbidden")

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", boom)
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "403" in body["detail"]


def test_sync_reports_bad_reference_without_raising(admin_client, site_payload, monkeypatch):
    patch_site_api(monkeypatch, reference_html="<p>текст без картинок</p>")
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "ни одной картинки" in body["detail"]


def test_site_list_exposes_readiness(admin_client, manager_client, site_payload, monkeypatch):
    """Менеджеру важно одно: можно ли по этому сайту запускать партию."""
    patch_site_api(monkeypatch)
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    assert manager_client.get("/api/sites").json()[0]["is_ready"] is False

    admin_client.post(f"/api/admin/sites/{site_id}/sync")
    ready = manager_client.get("/api/sites").json()[0]
    assert ready["is_ready"] is True
    assert ready["reference_images"] == 2


def test_watermark_upload_stores_file(admin_client, site_payload, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.admin_sites.config.media_dir", str(tmp_path))
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    resp = admin_client.post(f"/api/admin/sites/{site_id}/watermark",
                             files={"file": ("mark.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert resp.status_code == 200
    stored = tmp_path / "watermarks" / f"{site_id}.png"
    assert stored.exists()
