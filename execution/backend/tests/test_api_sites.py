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
    }


def test_manager_creates_site(manager_client, site_payload):
    resp = manager_client.post("/api/admin/sites", json=site_payload)
    assert resp.status_code == 200
    assert resp.json()["domain"] == "stroybaza-samara.ru"


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
    assert body == {
        "ok": True,
        "articles_ok": True, "articles_detail": "",
        "url_prefix": "/poleznye-stati/", "pages": 1, "reference_images": 3,
        "builder_ok": None, "builder_detail": "",
    }


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
    assert "403" in body["articles_detail"]


def test_sync_reports_bad_reference_without_raising(admin_client, site_payload, monkeypatch):
    patch_site_api(monkeypatch, reference_html="<p>текст без картинок</p>")
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "ни одной картинки" in body["articles_detail"]


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


# --- ретраи и атомарность (замечания ревью Task 11) ---


def test_sync_retries_on_5xx_and_succeeds(admin_client, site_payload, monkeypatch):
    """5xx на сайте — временная штука (перегрузка, деплой). Первая попытка
    проваливается, вторая — та же самая — проходит."""
    from app.sites.client import SiteAPIError

    calls = {"list": 0}

    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": "/poleznye-stati/"}
        return {"id": page_id, "text": "<img><img>"}

    def list_section_pages(self, prefix):
        calls["list"] += 1
        if calls["list"] == 1:
            raise SiteAPIError("список страниц: HTTP 500: боль", status_code=500)
        return [{"id": 1, "title": "A", "url": prefix + "a/"}]

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages", list_section_pages)
    monkeypatch.setattr("app.api.admin_sites.time.sleep", lambda seconds: None)

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is True
    assert calls["list"] == 2


def test_sync_does_not_retry_on_404(admin_client, site_payload, monkeypatch):
    """404 — не тот id страницы. Повтор с тем же запросом даст тот же 404,
    поэтому счётчик вызовов обязан остаться на единице."""
    from app.sites.client import SiteAPIError

    calls = {"n": 0}

    def get_page(self, page_id):
        calls["n"] += 1
        raise SiteAPIError("страница 25: HTTP 404: Not Found", status_code=404)

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.time.sleep", lambda seconds: None)

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "404" in body["articles_detail"]
    assert calls["n"] == 1


def test_sync_retries_on_network_error(admin_client, site_payload, monkeypatch):
    """status_code=None — сетевой сбой или сайт вернул не JSON: та же
    категория, что и 5xx, тоже повторяется."""
    from app.sites.client import SiteAPIError

    calls = {"n": 0}

    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": "/poleznye-stati/"}
        calls["n"] += 1
        if calls["n"] == 1:
            raise SiteAPIError("страница 312: сайт вернул не JSON: <html>...")
        return {"id": page_id, "text": "<img><img>"}

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages",
                        lambda self, prefix: [])
    monkeypatch.setattr("app.api.admin_sites.time.sleep", lambda seconds: None)

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is True
    assert calls["n"] == 2


def test_sync_gives_up_after_max_retries(admin_client, site_payload, monkeypatch):
    """Повторяющийся 5xx исчерпывает попытки и завершается отказом, а не
    бесконечным циклом."""
    from app.sites.client import SiteAPIError

    calls = {"n": 0}

    def list_section_pages(self, prefix):
        calls["n"] += 1
        raise SiteAPIError("список страниц: HTTP 503: боль", status_code=503)

    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.get_page",
        lambda self, page_id: {"id": page_id, "url": "/poleznye-stati/", "text": "<img>"})
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages", list_section_pages)
    monkeypatch.setattr("app.api.admin_sites.time.sleep", lambda seconds: None)

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert calls["n"] == 3


def test_sync_failure_leaves_site_fields_unchanged(admin_client, db_session, site_payload,
                                                    monkeypatch):
    """Синхронизация — одна транзакция: если обход раздела не удался (после
    того как эталон уже был бы готов записаться), в БД не должно появиться
    ни эталона, ни префикса — иначе отчёт "не получилось" врёт о состоянии
    сайта."""
    from app.models.site import Site
    from app.sites.client import SiteAPIError

    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.get_page",
        lambda self, page_id: {"id": page_id, "url": "/poleznye-stati/", "text": "<img><img>"})
    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.list_section_pages",
        lambda self, prefix: (_ for _ in ()).throw(
            SiteAPIError("список страниц: HTTP 404: Not Found", status_code=404)))

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]

    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False

    db_session.expire_all()
    site = db_session.get(Site, site_id)
    assert site.articles_url_prefix == ""
    assert site.reference_html == ""
    assert site.reference_images == 0
    assert site.reference_synced_at is None


def test_sync_failure_preserves_previous_successful_cache(admin_client, db_session,
                                                           site_payload, monkeypatch):
    """Кеш эталона от прошлой успешной синхронизации не должен затираться,
    если следующая синхронизация не удалась — иначе один сетевой сбой
    оставляет сайт вовсе без эталона, и статьи станет не по чему собирать."""
    from app.models.site import Site
    from app.sites.client import SiteAPIError

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]

    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.get_page",
        lambda self, page_id: {"id": page_id, "url": "/poleznye-stati/",
                               "text": "<p>t</p><img><img>"})
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages",
                        lambda self, prefix: [])
    first = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert first["ok"] is True

    db_session.expire_all()
    site = db_session.get(Site, site_id)
    old_prefix = site.articles_url_prefix
    old_images = site.reference_images
    old_html = site.reference_html
    old_synced_at = site.reference_synced_at
    assert old_images == 2

    def boom(self, prefix):
        raise SiteAPIError("список страниц: HTTP 404: Not Found", status_code=404)

    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages", boom)
    second = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert second["ok"] is False

    db_session.expire_all()
    site = db_session.get(Site, site_id)
    assert site.articles_url_prefix == old_prefix
    assert site.reference_images == old_images
    assert site.reference_html == old_html
    assert site.reference_synced_at == old_synced_at


_VALID_BUILDER_TEMPLATE = (
    '<div id="builder">'
    '<h1 id="builder-main-title"></h1>'
    '<div id="builder-contacts">'
    '<div id="builder-contacts-grid">'
    '<div id="builder-contact-1"></div>'
    '</div></div></div>'
)


def test_sync_skips_builder_step_when_not_configured(admin_client, site_payload, monkeypatch):
    """site_payload не задаёт builder_reference_id — шаг строителей должен
    молча пропускаться, не мешая шагу статей."""
    patch_site_api(monkeypatch)
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is True
    assert body["builder_ok"] is None
    assert body["builder_detail"] == ""


def test_sync_fills_builder_template_when_configured(admin_client, site_payload, monkeypatch):
    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": "/poleznye-stati/"}
        if page_id == 77:
            return {"id": 77, "text": _VALID_BUILDER_TEMPLATE}
        return {"id": page_id, "text": "<img><img>"}

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages",
                        lambda self, prefix: [])

    site_id = admin_client.post(
        "/api/admin/sites", json={**site_payload, "builder_reference_id": 77}).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is True
    assert body["builder_ok"] is True
    assert body["builder_detail"] == ""


def test_sync_reports_builder_failure_independently_of_articles(admin_client, site_payload,
                                                                 monkeypatch):
    """Эталон статьи валиден, эталон строителя — нет: итог должен показать
    оба результата раздельно, не теряя успех статей за отказом строителей."""
    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": "/poleznye-stati/"}
        if page_id == 77:
            return {"id": 77, "text": "<p>не тот контракт</p>"}
        return {"id": page_id, "text": "<img><img>"}

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages",
                        lambda self, prefix: [])

    site_id = admin_client.post(
        "/api/admin/sites", json={**site_payload, "builder_reference_id": 77}).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert body["articles_ok"] is True
    assert body["builder_ok"] is False
    assert "builder-main-title" in body["builder_detail"]
