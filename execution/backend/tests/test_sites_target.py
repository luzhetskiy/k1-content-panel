"""Цель публикации: статичные страницы против раздела /api/v1/articles/.

Дизайн и живая проверка схемы раздела —
directions/2026-09-23-articles-target-design.md.
"""

import pytest

from app.sites.client import SLUG_LIMIT_ARTICLES, SLUG_LIMIT_PAGES, SiteAPIError
from app.sites.target import (
    ArticlesTarget,
    PagesTarget,
    build_teaser,
    make_target,
    pick_label,
    require_label,
)


class FakeClient:
    def __init__(self, articles=None, pages=None):
        self._articles = articles or []
        self._pages = pages or []
        self.list_calls = 0
        self.created = None
        self.cover = None
        self.updated = None

    def list_articles(self):
        self.list_calls += 1
        return self._articles

    def list_section_pages(self, prefix):
        self.list_calls += 1
        return self._pages

    def get_article(self, article_id):
        return {"id": article_id, "body": "<p>эталон статьи</p>", "label": "Полезное"}

    def get_page(self, page_id):
        return {"id": page_id, "text": "<p>эталон страницы</p>"}

    def create_article(self, title, slug, html, teaser, label,
                       meta_description, meta_keywords):
        self.created = dict(title=title, slug=slug, html=html, teaser=teaser, label=label,
                            meta_description=meta_description, meta_keywords=meta_keywords)
        return {"id": 77}

    def create_page(self, title, url, html, parent_id, meta_description, meta_keywords):
        self.created = dict(title=title, url=url, parent_id=parent_id)
        return {"id": 77, "url": url}

    def set_article_cover(self, article_id, data, filename):
        self.cover = ("article", article_id, filename)

    def set_page_cover(self, page_id, data, filename):
        self.cover = ("page", page_id, filename)

    def update_article_text(self, article_id, html, *, title=None, teaser=None,
                            meta_description=None, meta_keywords=None):
        self.updated = dict(id=article_id, html=html, title=title, teaser=teaser,
                            meta_description=meta_description, meta_keywords=meta_keywords)

    def update_page_text(self, page_id, html, *, title=None,
                         meta_description=None, meta_keywords=None):
        self.updated = dict(id=page_id, html=html, title=title,
                            meta_description=meta_description, meta_keywords=meta_keywords)


def make_site(db_session, **kwargs):
    from app.models.site import Site

    fields = dict(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                  reference_html="<p>эталон</p>", reference_images=2)
    fields.update(kwargs)
    site = Site(**fields)
    db_session.add(site)
    db_session.commit()
    return site


ARTICLES = [
    {"id": 11, "title": "Масла для дерева", "slug": "zashita-drevesiny", "label": "Полезное"},
    {"id": 12, "title": "Клей Baumit", "slug": "klej-baumit", "label": "Рекомендации"},
    {"id": 13, "title": "Мембраны", "slug": "membrani", "label": "Полезное"},
]


def test_make_target_reads_publish_target(db_session):
    pages = make_site(db_session, publish_target="pages", domain="p.ru")
    articles = make_site(db_session, publish_target="articles", domain="a.ru")
    assert isinstance(make_target(pages, FakeClient()), PagesTarget)
    assert isinstance(make_target(articles, FakeClient()), ArticlesTarget)


def test_slug_limits_differ(db_session):
    """Живая проверка схемы: max_length слага в разделе articles — 50, у
    статичных страниц адреса обрезаны примерно на 70."""
    pages = make_site(db_session, publish_target="pages", domain="p.ru")
    articles = make_site(db_session, publish_target="articles", domain="a.ru")
    assert make_target(pages, FakeClient()).slug_limit == SLUG_LIMIT_PAGES
    assert make_target(articles, FakeClient()).slug_limit == SLUG_LIMIT_ARTICLES


def test_articles_public_path_is_built_from_slug(db_session):
    """У раздела articles поля url нет вообще — адрес собирает движок как
    /articles/<slug>/, и панель обязана считать его так же, иначе ссылка на
    черновик в таблице партии ведёт в никуда."""
    site = make_site(db_session, publish_target="articles")
    assert make_target(site, FakeClient()).public_path("membrani") == "/articles/membrani/"


def test_pages_public_path_uses_synced_prefix(db_session):
    site = make_site(db_session, publish_target="pages",
                     articles_url_prefix="/poleznye-stati/")
    assert make_target(site, FakeClient()).public_path("x") == "/poleznye-stati/x/"


def test_articles_site_is_synced_without_url_prefix(db_session):
    """articles_url_prefix у этой ветки пустой по устройству (префикс —
    константа в коде). Проверка синхронизации, завязанная на него, объявила бы
    такой сайт неготовым навсегда — партию на него завести было бы нельзя."""
    site = make_site(db_session, publish_target="articles", articles_url_prefix="")
    assert make_target(site, FakeClient()).is_synced() is True


def test_pages_site_without_prefix_is_not_synced(db_session):
    site = make_site(db_session, publish_target="pages", articles_url_prefix="")
    assert make_target(site, FakeClient()).is_synced() is False


def test_articles_site_without_reference_is_not_synced(db_session):
    site = make_site(db_session, publish_target="articles",
                     reference_html="", reference_images=0)
    assert make_target(site, FakeClient()).is_synced() is False


def test_articles_taken_addresses_come_from_slugs(db_session):
    """Дедуп у этой ветки идёт по слагу: url в списке раздела не приходит."""
    site = make_site(db_session, publish_target="articles")
    target = make_target(site, FakeClient(articles=ARTICLES))
    assert target.taken_addresses() == {"/articles/zashita-drevesiny/",
                                        "/articles/klej-baumit/",
                                        "/articles/membrani/"}


def test_existing_list_is_fetched_once(db_session):
    """Список спрашивают дважды — дедуп и выбор рубрики. Второй поход на сайт
    за теми же данными не нужен: между этими шагами ничего не публикуется."""
    site = make_site(db_session, publish_target="articles")
    client = FakeClient(articles=ARTICLES)
    target = make_target(site, client)
    target.taken_addresses()
    target.labels()
    target.list_existing()
    assert client.list_calls == 1


def test_attach_resets_cached_list(db_session):
    site = make_site(db_session, publish_target="articles")
    target = make_target(site, FakeClient(articles=ARTICLES))
    target.list_existing()
    target.attach(FakeClient(articles=[]))
    assert target.list_existing() == []


def test_labels_are_unique_in_order_of_appearance(db_session):
    site = make_site(db_session, publish_target="articles")
    target = make_target(site, FakeClient(articles=ARTICLES))
    assert target.labels() == ["Полезное", "Рекомендации"]


def test_pages_target_has_no_labels(db_session):
    site = make_site(db_session, publish_target="pages",
                     articles_url_prefix="/poleznye-stati/")
    assert make_target(site, FakeClient(pages=[])).labels() == []


def test_pick_label_matches_ignoring_case_and_quotes(db_session):
    site = make_site(db_session, publish_target="articles")
    target = make_target(site, FakeClient(articles=ARTICLES))
    assert pick_label('«рекомендации»', target) == "Рекомендации"
    assert pick_label("  Полезное  ", target) == "Полезное"


def test_pick_label_falls_back_to_reference_label(db_session):
    """Модель придумала рубрику, которой в разделе нет. Записать её как есть
    нельзя: сайт заведёт новую рубрику одним фактом записи, и опечатка модели
    останется в списке рубрик навсегда."""
    site = make_site(db_session, publish_target="articles", reference_article_id=12)
    target = make_target(site, FakeClient(articles=ARTICLES))
    assert pick_label("Про материалы", target) == "Рекомендации"


def test_pick_label_falls_back_to_first_when_reference_missing(db_session):
    site = make_site(db_session, publish_target="articles", reference_article_id=999)
    target = make_target(site, FakeClient(articles=ARTICLES))
    assert pick_label("Про материалы", target) == "Полезное"


def test_require_label_rejects_empty_with_readable_text():
    """Пустая рубрика означает раздел без единой статьи. Сайт ответил бы на
    это HTTP 400, который менеджеру ничего не говорит."""
    with pytest.raises(SiteAPIError) as exc:
        require_label("")
    assert "рубрик" in str(exc.value)


def test_require_label_truncates_to_field_limit():
    assert len(require_label("о" * 80)) == 60


def test_build_teaser_wraps_meta_description():
    assert build_teaser("Чем утеплить дом", "Заголовок") == "<p>Чем утеплить дом</p>"


def test_build_teaser_falls_back_to_title():
    """teaser обязателен на стороне сайта, а _generate_body требует от модели
    только html — meta_description может не прийти вовсе."""
    assert build_teaser("", "Заголовок") == "<p>Заголовок</p>"


def test_build_teaser_escapes_html():
    assert build_teaser("Утеплитель <50 мм & плёнка", "T") == \
        "<p>Утеплитель &lt;50 мм &amp; плёнка</p>"


def test_articles_reference_reads_body_field(db_session):
    """Эталон для этой ветки — запись раздела articles, а её разметка лежит в
    body; поля text, как у staticpages, у неё нет."""
    site = make_site(db_session, publish_target="articles")
    assert make_target(site, FakeClient()).reference(11) == {
        "html": "<p>эталон статьи</p>", "label": "Полезное"}


def test_pages_reference_reads_text_field(db_session):
    site = make_site(db_session, publish_target="pages",
                     articles_url_prefix="/poleznye-stati/")
    assert make_target(site, FakeClient()).reference(11)["html"] == "<p>эталон страницы</p>"


def test_articles_create_sends_slug_teaser_and_label(db_session):
    from types import SimpleNamespace

    site = make_site(db_session, publish_target="articles")
    client = FakeClient(articles=ARTICLES)
    article = SimpleNamespace(title="Чем утеплить дом", slug="chem-uteplit-dom",
                              body_html="<p>текст</p>", meta_description="описание",
                              meta_keywords="утепление")
    make_target(site, client).create(article, teaser="<p>описание</p>", label="Полезное")
    assert client.created["slug"] == "chem-uteplit-dom"
    assert client.created["teaser"] == "<p>описание</p>"
    assert client.created["label"] == "Полезное"
    assert "url" not in client.created and "parent_id" not in client.created


def test_articles_cover_goes_to_image_field(db_session):
    site = make_site(db_session, publish_target="articles")
    client = FakeClient()
    make_target(site, client).set_cover(77, b"webp", "cp-article-1-cover.webp")
    assert client.cover == ("article", 77, "cp-article-1-cover.webp")


def test_articles_update_text_refreshes_teaser_with_meta(db_session):
    """Тизер собран из meta_description. Перегенерация текста меняет его
    источник — без обновления тизера на карточке статьи остался бы анонс от
    прошлой версии."""
    site = make_site(db_session, publish_target="articles")
    client = FakeClient()
    make_target(site, client).update_text(77, "<p>новый</p>", title="Новый заголовок",
                                          meta_description="новое описание",
                                          meta_keywords="ключи")
    assert client.updated["teaser"] == "<p>новое описание</p>"


def test_articles_update_text_keeps_teaser_when_meta_not_touched(db_session):
    """Перегенерация одних картинок меняет только пути в теле — тизеру
    меняться не с чего."""
    site = make_site(db_session, publish_target="articles")
    client = FakeClient()
    make_target(site, client).update_text(77, "<p>новые пути</p>")
    assert client.updated["teaser"] is None
