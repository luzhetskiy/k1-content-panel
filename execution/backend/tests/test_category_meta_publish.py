import pytest

from app.category_meta.publish import publish_metatag
from app.models.category_meta import CategoryMeta
from app.sites.client import SiteAPIError

TAGS = {"title": "Фанера в Москве — купить | Стройбаза", "h1": "Фанера в Москве",
        "meta_description": "описание", "meta_keywords": "фанера", "ai_keywords": "где купить",
        "seo_text": "<h2>Фанера</h2><p>текст</p>"}
URL = "/catalog/category/listovye-materialy/fanera/"


class FakeSite:
    def __init__(self, metatags=None, create_errors=None, update_errors=None):
        self.metatags = list(metatags or [])
        self.create_errors = list(create_errors or [])
        self.update_errors = list(update_errors or [])
        self.created, self.updated = [], []

    def list_metatags(self):
        return [dict(item) for item in self.metatags]

    def create_metatag(self, url, fields):
        item = {"id": 100 + len(self.metatags), "url": url, **fields}
        self.metatags.append(item)      # запись появляется, даже если ответ «не дошёл»
        self.created.append((url, fields))
        if self.create_errors:
            raise self.create_errors.pop(0)
        return item

    def update_metatag(self, metatag_id, fields):
        if self.update_errors:
            raise self.update_errors.pop(0)
        self.updated.append((metatag_id, fields))
        return {"id": metatag_id}


def category():
    return CategoryMeta(remote_id=46, name="Фанера", url=URL)


def test_existing_metatag_is_patched_and_previous_saved():
    site = FakeSite([{"id": 39, "url": URL, "title": "", "h1": "Фанера любых видов",
                      "meta_description": None, "meta_keywords": "", "ai_keywords": "",
                      "seo_text": "<p>текст</p>"}])
    row = category()
    publish_metatag(site, row, TAGS, sleep=lambda s: None)
    assert site.updated == [(39, TAGS)]
    assert site.created == []
    assert row.remote_metatag_id == 39
    assert row.previous_json == {"title": "", "h1": "Фанера любых видов", "meta_description": "",
                                 "meta_keywords": "", "ai_keywords": "",
                                 "seo_text": "<p>текст</p>"}


def test_only_seo_text_is_patched_when_tags_are_ready():
    site = FakeSite([{"id": 39, "url": URL, **TAGS, "seo_text": ""}])
    row = category()
    row.previous_json = {"title": ""}
    publish_metatag(site, row, TAGS, ("seo_text",), sleep=lambda s: None)
    assert site.updated == [(39, {"seo_text": TAGS["seo_text"]})]


def test_missing_metatag_is_created():
    site = FakeSite([{"id": 1, "url": "/"}])
    row = category()
    publish_metatag(site, row, TAGS, sleep=lambda s: None)
    assert site.created == [(URL, TAGS)]
    assert row.previous_json == {}
    assert row.remote_metatag_id == 101


def test_previous_json_is_not_overwritten_by_our_own_values():
    site = FakeSite([{"id": 39, "url": URL, **TAGS}])
    row = category()
    row.previous_json = {"title": "", "h1": "Фанера любых видов", "meta_description": "",
                         "meta_keywords": "", "ai_keywords": ""}
    publish_metatag(site, row, TAGS, sleep=lambda s: None)
    assert row.previous_json["h1"] == "Фанера любых видов"


def test_lost_create_response_does_not_duplicate():
    site = FakeSite(create_errors=[SiteAPIError("создание метатега: сеть недоступна")])
    row = category()
    publish_metatag(site, row, TAGS, sleep=lambda s: None)
    assert len(site.created) == 1
    assert [item["url"] for item in site.metatags] == [URL]
    assert site.updated == [(100, TAGS)]


def test_client_error_is_not_retried():
    site = FakeSite([{"id": 39, "url": URL}],
                    update_errors=[SiteAPIError("HTTP 400", status_code=400)])
    with pytest.raises(SiteAPIError):
        publish_metatag(site, category(), TAGS, sleep=lambda s: None)
    assert site.updated == []


def test_server_error_retried_then_raised():
    sleeps = []
    site = FakeSite([{"id": 39, "url": URL}],
                    update_errors=[SiteAPIError("HTTP 502", status_code=502)] * 3)
    with pytest.raises(SiteAPIError):
        publish_metatag(site, category(), TAGS, sleep=sleeps.append)
    assert sleeps == [1, 2]
