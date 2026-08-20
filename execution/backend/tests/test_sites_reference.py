import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.sites.client import SiteAPIError
from app.sites.reference import (
    ReferenceError,
    count_images,
    extract_image_srcs,
    measure_reference_image_ratios,
    sync_site_reference,
)


def png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(buffer, "PNG")
    return buffer.getvalue()


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
    def __init__(self, parent_url="/poleznye-stati/",
                reference_html="<img><img>", files=None):
        self.parent_url = parent_url
        self.reference_html = reference_html
        self.requested = []
        # url -> bytes; fetch_file на незарегистрированный url бросает
        # SiteAPIError — тот же класс ошибки, что и у боевого SiteClient
        # на сетевой сбой/4xx/5xx (см. её докстринг).
        self.files = files or {}

    def get_page(self, page_id):
        self.requested.append(page_id)
        if page_id == 25:
            return {"id": 25, "url": self.parent_url, "text": "<p>раздел</p>"}
        return {"id": page_id, "url": "/poleznye-stati/etalon/",
                "text": self.reference_html}

    def list_section_pages(self, prefix):
        return [{"id": 9, "title": "Старая", "url": prefix + "staraya/"}]

    def fetch_file(self, url):
        if url not in self.files:
            raise SiteAPIError(f"файл {url}: HTTP 404: not found", status_code=404)
        return self.files[url]


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


# --- пропорции картинок эталона (найдено на stroybaza-moscow.ru: первая
# картинка статьи рендерится в широком .article-hero, а генератор кадрировал
# её в 3:2, как и все остальные, — получалось заметно выше эталона) ---


def test_extract_image_srcs_returns_in_order():
    html = "<img src='a.webp'><p>x</p><img src='b.webp'>"
    assert extract_image_srcs(html) == ["a.webp", "b.webp"]


def test_extract_image_srcs_none_for_missing_src():
    # Тестовые заглушки sync_site_reference (FakeClient(reference_html=
    # "<img><img>")) — <img> без атрибутов вообще, это не экзотика для
    # тестов, а реальный случай, который нельзя ронять исключением.
    assert extract_image_srcs("<img><img src='b.webp'>") == [None, "b.webp"]


def test_extract_image_srcs_ignores_img_inside_comment():
    assert extract_image_srcs("<img src='a.webp'><!-- <img src='b.webp'> -->") == ["a.webp"]


def test_extract_image_srcs_matches_count_images_length():
    """Индексы измеренных пропорций (measure_reference_image_ratios) обязаны
    совпадать с позициями картинок статьи (1..count_images) — расхождение в
    длине незаметно сдвинуло бы пропорции на чужие позиции."""
    html = "<p>t</p><img src='a.webp'><img><img src='c.webp'>"
    assert len(extract_image_srcs(html)) == count_images(html)


def test_measure_reference_image_ratios_uses_real_pixel_dimensions():
    html = "<img src='/media/hero.webp'><img src='/media/square.webp'>"
    client = FakeClient(files={
        "/media/hero.webp": png_bytes(1180, 488),
        "/media/square.webp": png_bytes(631, 631),
    })
    assert measure_reference_image_ratios(client, html) == ["1180:488", "631:631"]


def test_measure_reference_image_ratios_falls_back_to_empty_when_unreachable():
    """Эталон — чужой сайт, не наш API: временная недоступность одной
    картинки не должна ронять синхронизацию целиком (см. докстринг
    measure_reference_image_ratios) — просто эта позиция кадрируется потом
    дефолтным CONTENT_CROP."""
    html = "<img src='/media/broken.webp'><img src='/media/hero.webp'>"
    client = FakeClient(files={"/media/hero.webp": png_bytes(1180, 488)})
    assert measure_reference_image_ratios(client, html) == ["", "1180:488"]


def test_measure_reference_image_ratios_falls_back_to_empty_for_missing_src():
    client = FakeClient(files={"/media/hero.webp": png_bytes(1180, 488)})
    assert measure_reference_image_ratios(client, "<img><img src='/media/hero.webp'>") == \
        ["", "1180:488"]


def test_measure_reference_image_ratios_falls_back_when_content_is_not_an_image():
    """fetch_file может успешно вернуть 200 с мусором вместо картинки
    (например, страница логина) — PIL здесь бросит не OSError/SiteAPIError
    конкретно, а любую ошибку разбора; любая из них не должна ронять
    измерение остальных позиций (см. докстринг measure_reference_image_ratios)."""
    html = "<img src='/media/not-an-image.webp'><img src='/media/hero.webp'>"
    client = FakeClient(files={
        "/media/not-an-image.webp": b"<html>not an image</html>",
        "/media/hero.webp": png_bytes(1180, 488),
    })
    assert measure_reference_image_ratios(client, html) == ["", "1180:488"]


def test_sync_stores_reference_image_ratios(db_session, site):
    html = "<p>t</p><img src='/media/hero.webp'><img src='/media/square.webp'>"
    client = FakeClient(reference_html=html, files={
        "/media/hero.webp": png_bytes(1180, 488),
        "/media/square.webp": png_bytes(631, 631),
    })
    sync_site_reference(db_session, site, client)
    assert site.reference_image_ratios == "1180:488,631:631"


def test_sync_stores_empty_ratios_when_images_use_bare_img_tags(db_session, site):
    """Существующее поведение (FakeClient по умолчанию, другие тесты этого
    файла) не должно сломаться из-за новой логики измерения: <img> без src
    просто не измеряется, синхронизация не падает."""
    sync_site_reference(db_session, site, FakeClient(reference_html="<p>t</p><img><img>"))
    assert site.reference_image_ratios == ","
