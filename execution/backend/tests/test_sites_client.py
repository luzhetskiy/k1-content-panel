import json
from unittest.mock import Mock, patch

import pytest

from app.sites.client import SiteAPIError, SiteClient, slugify


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", json_error=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {}
        self._json_error = json_error
        self.text = text

    def json(self):
        if self._json_error:
            # requests.Response.json() поднимает подкласс ValueError
            # (json.JSONDecodeError или requests.exceptions.JSONDecodeError,
            # который сам от него унаследован) — воспроизводим это же исключение.
            raise json.JSONDecodeError("Expecting value", self.text or "", 0)
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


# --- SiteAPIError несёт статус ответа: 404 в исключении при HTTP-ошибке,
# None при ошибке разбора тела (сайт вернул 200 с мусором) или сетевом сбое. ---

def test_error_status_code_is_exposed_on_http_error(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda *a, **kw: FakeResponse(404, text="Not Found"))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").create_page(
            title="T", url="/blog/x/", html="<p>a</p>", parent_id=25)
    assert exc_info.value.status_code == 404


# --- «200 OK с мусором внутри»: HTML прокси, обрезанный JSON или пустое тело
# при успешном статусе не должны долетать до вызывающего голым
# json.JSONDecodeError — только SiteAPIError с status_code=None. Тот же класс
# дефекта уже закрыт в app/ai/images.py для ответов RouterAI. ---

_BAD_BODIES = pytest.mark.parametrize("bad_text", [
    "<html><body>Please log in</body></html>",   # HTML вместо JSON
    '{"id": 1, "url": "/blog/x/"',                # обрезанный JSON
    "",                                            # пустое тело
], ids=["html", "truncated-json", "empty"])


@_BAD_BODIES
def test_list_section_pages_rejects_invalid_json(monkeypatch, bad_text):
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda *a, **kw: FakeResponse(200, text=bad_text, json_error=True))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").list_section_pages("/blog/")
    assert exc_info.value.status_code is None


@_BAD_BODIES
def test_get_page_rejects_invalid_json(monkeypatch, bad_text):
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda *a, **kw: FakeResponse(200, text=bad_text, json_error=True))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").get_page(77)
    assert exc_info.value.status_code is None


@_BAD_BODIES
def test_create_page_rejects_invalid_json(monkeypatch, bad_text):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda *a, **kw: FakeResponse(200, text=bad_text, json_error=True))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").create_page(
            title="T", url="/blog/x/", html="<p>a</p>", parent_id=25)
    assert exc_info.value.status_code is None


@_BAD_BODIES
def test_set_page_cover_rejects_invalid_json(monkeypatch, bad_text):
    monkeypatch.setattr("app.sites.client.requests.patch",
                        lambda *a, **kw: FakeResponse(200, text=bad_text, json_error=True))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").set_page_cover(77, b"img", "cover.webp")
    assert exc_info.value.status_code is None


# --- upload_file обязан поднимать SiteAPIError на ошибочном статусе — раньше
# проверялось только для create_page. ---

@pytest.mark.parametrize("status", [413, 500])
def test_upload_file_raises_on_error_status(monkeypatch, status):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda *a, **kw: FakeResponse(status, text="oops"))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").upload_file(b"d", "a.webp", "uploads/article-img/")
    assert exc_info.value.status_code == status


# --- таймауты: все методы обязаны передавать числовой таймаут в requests,
# а не None (бесконечное ожидание съедает слот воркера часами). ---

def test_all_methods_use_numeric_timeout(monkeypatch):
    captured = []

    def fake_get(url, **kwargs):
        captured.append(kwargs.get("timeout"))
        return FakeResponse(200, {"results": [], "next": None})

    def fake_post(url, **kwargs):
        captured.append(kwargs.get("timeout"))
        return FakeResponse(201, {"id": 1, "url": "/blog/x/"})

    def fake_patch(url, **kwargs):
        captured.append(kwargs.get("timeout"))
        return FakeResponse(200, {"teaser_image": "/media/x.webp"})

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    monkeypatch.setattr("app.sites.client.requests.post", fake_post)
    monkeypatch.setattr("app.sites.client.requests.patch", fake_patch)

    client = SiteClient("https://x.ru", "token")
    client.list_section_pages("/blog/")
    client.get_page(1)
    client.create_page(title="T", url="/blog/x/", html="<p>a</p>", parent_id=25)
    client.set_page_cover(1, b"img", "cover.webp")
    client.upload_file(b"data", "a.webp", "uploads/article-img/")

    assert len(captured) == 5
    for value in captured:
        assert isinstance(value, (int, float)) and value > 0


def test_timeout_and_upload_timeout_are_independently_configurable(monkeypatch):
    """set_page_cover/upload_file раньше игнорировали self.timeout из
    конструктора и жёстко использовали 120 — несогласованность, а не
    решение. Теперь у загрузки свой явный параметр конструктора."""
    captured = {}

    def fake_get(url, **kwargs):
        captured["get"] = kwargs.get("timeout")
        return FakeResponse(200, {"results": [], "next": None})

    def fake_post(url, **kwargs):
        captured["upload"] = kwargs.get("timeout")
        return FakeResponse(200, {}, text="Success")

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    monkeypatch.setattr("app.sites.client.requests.post", fake_post)

    client = SiteClient("https://x.ru", "token", timeout=45, upload_timeout=200)
    client.list_section_pages("/blog/")
    client.upload_file(b"d", "a.webp", "uploads/article-img/")

    assert captured["get"] == 45
    assert captured["upload"] == 200


def test_create_teaser_posts_expected_payload():
    client = SiteClient("https://s.ru", "tok")
    response = Mock(ok=True, status_code=201)
    response.json.return_value = {"id": 42}
    with patch("app.sites.client.requests.post", return_value=response) as post:
        teaser_id = client.create_teaser(
            name="ООО Дом", slug="ooo-dom-samara", address="ул. Ленина 1",
            phone="79991234567", email="info@dom.ru", website="https://dom.ru",
            page_url="/s/ooo-dom-samara/", category=3, city=1, location=1,
        )
    assert teaser_id == 42
    payload = post.call_args.kwargs["json"]
    assert payload["slug"] == "ooo-dom-samara"
    assert payload["category"] == 3
    assert payload["is_active"] is False


def test_create_teaser_raises_on_error():
    from app.sites.client import SiteAPIError

    client = SiteClient("https://s.ru", "tok")
    response = Mock(ok=False, status_code=400, text="bad request")
    with patch("app.sites.client.requests.post", return_value=response):
        try:
            client.create_teaser(
                name="А", slug="a", address="", phone="", email="", website="",
                page_url="/s/a/", category=1, city=1, location=1,
            )
            assert False, "ожидался SiteAPIError"
        except SiteAPIError:
            pass
