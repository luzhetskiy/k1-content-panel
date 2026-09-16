import json

import pytest

from app.sites.client import SiteAPIError, SiteClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", content: bytes = b""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload if payload is not None else {}
        self.text = text
        self.content = content

    def json(self):
        return self._payload


def test_list_catalog_categories_follows_pages(monkeypatch):
    calls = []
    pages = {1: {"results": [{"id": 1}], "next": "https://s.ru/api/v1/catalog-categories/?page=2"},
             2: {"results": [{"id": 2}], "next": None}}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(payload=pages[int(url.rsplit("=", 1)[1])])

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    assert SiteClient("https://s.ru/", "tok").list_catalog_categories() == [{"id": 1}, {"id": 2}]
    assert calls[0][0] == "https://s.ru/api/v1/catalog-categories/?page=1"
    assert calls[0][1]["headers"]["Authorization"] == "Token tok"
    assert calls[0][1]["timeout"] == 120


def test_list_metatags(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda url, **kw: FakeResponse(payload={"results": [{"id": 3, "url": "/"}],
                                                                "next": None}))
    assert SiteClient("https://s.ru", "t").list_metatags() == [{"id": 3, "url": "/"}]


def test_create_metatag_posts_url_and_fields(monkeypatch):
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return FakeResponse(payload={"id": 41, "url": "/catalog/x/"})

    monkeypatch.setattr("app.sites.client.requests.post", fake_post)
    body = SiteClient("https://s.ru", "t").create_metatag("/catalog/x/", {"title": "T", "h1": "H"})
    assert body["id"] == 41
    assert sent["url"] == "https://s.ru/api/v1/metatags/"
    assert sent["json"] == {"url": "/catalog/x/", "title": "T", "h1": "H"}


def test_create_metatag_without_id_is_error(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda url, **kw: FakeResponse(payload={"detail": "ok"}))
    with pytest.raises(SiteAPIError):
        SiteClient("https://s.ru", "t").create_metatag("/x/", {})


def test_update_metatag_patches_by_id(monkeypatch):
    sent = {}

    def fake_patch(url, **kwargs):
        sent.update(url=url, **kwargs)
        return FakeResponse(payload={"id": 41})

    monkeypatch.setattr("app.sites.client.requests.patch", fake_patch)
    SiteClient("https://s.ru", "t").update_metatag(41, {"title": "T"})
    assert sent["url"] == "https://s.ru/api/v1/metatags/41/"
    assert sent["json"] == {"title": "T"}


def test_update_metatag_http_error(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.patch",
                        lambda url, **kw: FakeResponse(400, text="bad"))
    with pytest.raises(SiteAPIError) as err:
        SiteClient("https://s.ru", "t").update_metatag(41, {})
    assert err.value.status_code == 400


URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://s.ru/catalog/listovye-materialy/</loc></url>
<url><loc>https://s.ru/catalog/category/listovye-materialy/fanera/</loc></url>
<url><loc>https://s.ru/catalog/stroitelnye-smesi/?tip-smesi=gruntovka</loc></url>
</urlset>"""


def test_sitemap_paths_skip_filter_pages_and_send_no_token(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(content=URLSET)

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    paths = SiteClient("https://s.ru", "secret").fetch_sitemap_paths()
    assert paths == {"/catalog/listovye-materialy/",
                     "/catalog/category/listovye-materialy/fanera/"}
    assert calls[0][0] == "https://s.ru/sitemap.xml"
    assert "Authorization" not in calls[0][1]["headers"]


def test_sitemap_index_is_followed(monkeypatch):
    index = b"""<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap><loc>https://s.ru/sitemap-catalog.xml</loc></sitemap></sitemapindex>"""
    responses = {"https://s.ru/sitemap.xml": index, "https://s.ru/sitemap-catalog.xml": URLSET}
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda url, **kw: FakeResponse(content=responses[url]))
    assert "/catalog/listovye-materialy/" in SiteClient("https://s.ru", "t").fetch_sitemap_paths()


def test_sitemap_not_xml(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda url, **kw: FakeResponse(content=b"<html>cookie"))
    with pytest.raises(SiteAPIError):
        SiteClient("https://s.ru", "t").fetch_sitemap_paths()
