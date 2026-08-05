from types import SimpleNamespace

import pytest

from app.sites.reference import ReferenceError, count_images, sync_site_reference


def test_count_images_counts_img_tags():
    html = "<p>Текст</p><img src='a.webp'><p>Ещё</p><img src='b.webp'/>"
    assert count_images(html) == 2


def test_count_images_ignores_case_and_attributes():
    assert count_images('<IMG SRC="a.webp" class="x">') == 1


def test_count_images_ignores_img_inside_comment():
    """Закомментированная картинка не рендерится — считать её нельзя,
    иначе сгенерируем лишний кадр и заплатим за него."""
    assert count_images("<img src='a.webp'><!-- <img src='b.webp'> -->") == 1


def test_count_images_on_empty_html():
    assert count_images("") == 0


class FakeClient:
    def __init__(self, parent_url="/poleznye-stati/", reference_html="<img><img>"):
        self.parent_url = parent_url
        self.reference_html = reference_html
        self.requested = []

    def get_page(self, page_id):
        self.requested.append(page_id)
        if page_id == 25:
            return {"id": 25, "url": self.parent_url, "text": "<p>раздел</p>"}
        return {"id": page_id, "url": "/poleznye-stati/etalon/",
                "text": self.reference_html}

    def list_section_pages(self, prefix):
        return [{"id": 9, "title": "Старая", "url": prefix + "staraya/"}]


@pytest.fixture
def site(db_session):
    from app.models.site import Site

    row = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
               articles_parent_id=25, reference_article_id=312)
    db_session.add(row)
    db_session.commit()
    return row


def test_sync_derives_url_prefix_from_parent(db_session, site):
    """Префикс не вводится руками: он берётся с самой родительской страницы,
    поэтому не может разъехаться с тем, что на сайте."""
    sync_site_reference(db_session, site, FakeClient())
    assert site.articles_url_prefix == "/poleznye-stati/"


def test_sync_caches_reference_html_and_image_count(db_session, site):
    sync_site_reference(db_session, site, FakeClient(reference_html="<p>t</p><img><img><img>"))
    assert site.reference_images == 3
    assert "<p>t</p>" in site.reference_html
    assert site.reference_synced_at is not None


def test_sync_requires_parent_id(db_session, site):
    site.articles_parent_id = None
    db_session.commit()
    with pytest.raises(ReferenceError, match="родительск"):
        sync_site_reference(db_session, site, FakeClient())


def test_sync_requires_reference_article(db_session, site):
    site.reference_article_id = None
    db_session.commit()
    with pytest.raises(ReferenceError, match="Эталонная"):
        sync_site_reference(db_session, site, FakeClient())


def test_sync_rejects_reference_without_images(db_session, site):
    """Эталон без единой картинки означает, что статьи пойдут без иллюстраций —
    это почти всегда ошибка выбора эталона, а не осознанное решение."""
    with pytest.raises(ReferenceError, match="ни одной картинки"):
        sync_site_reference(db_session, site, FakeClient(reference_html="<p>только текст</p>"))


def test_sync_failure_does_not_clobber_previous_cache(db_session, site):
    """Отказ синхронизации (эталон без картинок) не должен стирать кеш от
    прошлой успешной синхронизации — иначе один плохой запуск оставляет сайт
    вовсе без эталона, и статьи станет не по чему собирать."""
    sync_site_reference(db_session, site, FakeClient(reference_html="<p>t</p><img><img>"))
    old_prefix = site.articles_url_prefix
    old_html = site.reference_html
    old_images = site.reference_images
    old_synced_at = site.reference_synced_at
    assert old_images == 2

    with pytest.raises(ReferenceError, match="ни одной картинки"):
        sync_site_reference(db_session, site, FakeClient(reference_html="<p>только текст</p>"))

    assert site.articles_url_prefix == old_prefix
    assert site.reference_html == old_html
    assert site.reference_images == old_images
    assert site.reference_synced_at == old_synced_at
