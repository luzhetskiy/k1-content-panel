import pytest

from app.sites.client import SiteAPIError, SiteClient, slugify


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_slugify_transliterates_and_limits():
    assert slugify("Как выбрать фундамент для дома") == "kak-vybrat-fundament-dlya-doma"
    assert len(slugify("а" * 200, limit=70)) == 70


def test_slugify_strips_punctuation():
    assert slugify("Дом: 5 ошибок!") == "dom-5-oshibok"


def test_list_section_pages_follows_pagination(monkeypatch):
    pages = {
        1: {"results": [{"id": 1, "title": "Статья A", "url": "/blog/a/"},
                        {"id": 2, "title": "О компании", "url": "/about/"}],
            "next": "?page=2"},
        2: {"results": [{"id": 3, "title": "Статья B", "url": "/blog/b/"}], "next": None},
    }
    seen = []

    def fake_get(url, **kwargs):
        page = 2 if "page=2" in url else 1
        seen.append(page)
        return FakeResponse(200, pages[page])

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    client = SiteClient("https://x.ru", "token")
    result = client.list_section_pages("/blog/")
    assert seen == [1, 2]
    assert [p["title"] for p in result] == ["Статья A", "Статья B"]


def test_create_page_sends_draft_payload(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, json=kwargs["json"])
        return FakeResponse(201, {"id": 77, "url": "/blog/test/"})

    monkeypatch.setattr("app.sites.client.requests.post", fake_post)
    client = SiteClient("https://x.ru", "token")
    created = client.create_page(title="Тест", url="/blog/test/", html="<p>x</p>",
                                 parent_id=25, meta_description="d", meta_keywords="k")
    assert created["id"] == 77
    assert captured["json"]["published"] is False
    assert captured["json"]["parent"] == 25
    assert captured["json"]["wide_view"] is True
    assert captured["json"]["use_editor"] is False


def test_create_page_strips_html_comments(monkeypatch):
    captured = {}
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda url, **kw: (captured.update(kw["json"]),
                                           FakeResponse(201, {"id": 1, "url": "/blog/x/"}))[1])
    SiteClient("https://x.ru", "t").create_page(
        title="T", url="/blog/x/", html="<p>a</p><!-- служебный -->", parent_id=25)
    assert "служебный" not in captured["text"]


def test_upload_file_builds_predictable_path(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, data=kwargs["data"])
        return FakeResponse(200, {}, text="Success")

    monkeypatch.setattr("app.sites.client.requests.post", fake_post)
    client = SiteClient("https://x.ru", "token")
    path = client.upload_file(b"data", "article_1-1.webp", "uploads/article-img/")
    # Путь не приходит в ответе — он предсказуем, потому что коллизия имени
    # в filemanager означает перезапись, а не суффикс.
    assert path == "/media/uploads/article-img/article_1-1.webp"
    assert captured["data"]["upload_to"] == "uploads/article-img/"


def test_upload_uses_token_header_not_stroyker_key(monkeypatch):
    """X-STROYKER-KEY из документации даёт 403 — рабочая авторизация
    обычным Token."""
    captured = {}
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda url, **kw: (captured.update(kw["headers"]),
                                           FakeResponse(200, {}, "Success"))[1])
    SiteClient("https://x.ru", "tok").upload_file(b"d", "a.webp", "uploads/article-img/")
    assert captured["Authorization"] == "Token tok"
    assert "X-STROYKER-KEY" not in captured


def test_set_page_cover_patches_teaser_image(monkeypatch):
    captured = {}

    def fake_patch(url, **kwargs):
        captured.update(url=url, files=kwargs["files"])
        return FakeResponse(200, {"teaser_image": "/media/staticpages/images/cover.webp"})

    monkeypatch.setattr("app.sites.client.requests.patch", fake_patch)
    client = SiteClient("https://x.ru", "token")
    result = client.set_page_cover(77, b"img", "cover.webp")
    assert captured["url"] == "https://x.ru/api/v1/staticpages/77/"
    assert "teaser_image" in captured["files"]
    assert result == "/media/staticpages/images/cover.webp"


def test_error_response_raises(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda *a, **kw: FakeResponse(403, text="Forbidden"))
    with pytest.raises(SiteAPIError, match="403"):
        SiteClient("https://x.ru", "bad").create_page(
            title="T", url="/blog/x/", html="<p>a</p>", parent_id=25)
