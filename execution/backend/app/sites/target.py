"""Цель публикации статьи: статичные страницы или раздел /api/v1/articles/.

У сайта есть настройка `publish_target` — `pages` или `articles`. Она влияет
на восемь точек: источник эталона при синхронизации, сбор заголовков для
генерации тем, признак «эталон синхронизирован», дедуп уже существующих
статей, создание черновика, прикрепление обложки, PATCH текста при
перегенерации и длину слага. Ветвление `if site.publish_target == ...` в
каждой из них разъехалось бы при первой правке, поэтому различия собраны
здесь, в двух реализациях одного интерфейса, а вызывающий код (ArticleBuilder,
sync_site_reference, задача генерации тем) работает с целью и про
`publish_target` не знает.

Дизайн и живая проверка схемы /api/v1/articles/ —
directions/2026-09-23-articles-target-design.md.
"""

from __future__ import annotations

import html as html_module

from app.models.site import Site
from app.sites.client import SLUG_LIMIT_ARTICLES, SLUG_LIMIT_PAGES, SiteAPIError

# Раздел статей у этого движка один и адрес собирает он сам: /articles/<slug>/.
# Вычислять здесь нечего — в отличие от ветки pages, где раздел произвольный и
# его префикс подтягивается с родительской страницы при синхронизации.
ARTICLES_URL_PREFIX = "/articles/"

LABEL_LIMIT = 60      # max_length поля label, живая проверка через OPTIONS


class PublishTarget:
    """Общая часть: кеш списка уже существующих записей.

    Цель живёт ровно столько, сколько сборка одной статьи, а список
    спрашивают дважды — дедуп и выбор рубрики. Кеш убирает второй поход на
    сайт; переживать за устаревание нечего, между этими двумя шагами ничего
    не публикуется.
    """

    kind = ""
    slug_limit = SLUG_LIMIT_PAGES

    def __init__(self, site: Site, client=None):
        # client необязателен: часть вопросов к цели — про саму карточку
        # сайта, а не про сайт (is_synced, url_prefix, slug_limit), и
        # спрашивающим их (app/api/sites.py) клиент не нужен и неоткуда
        # взять — токен там сознательно не расшифровывается.
        self.site = site
        self.client = client
        self._existing: list[dict] | None = None

    def attach(self, client) -> None:
        """Сменить клиента сайта вместе со сбросом кеша: список, вычитанный
        прежним клиентом, новому не принадлежит."""
        self.client = client
        self._existing = None

    def list_existing(self) -> list[dict]:
        if self._existing is None:
            self._existing = self._fetch_existing()
        return self._existing

    def _fetch_existing(self) -> list[dict]:
        raise NotImplementedError

    def labels(self) -> list[str]:
        return []


class PagesTarget(PublishTarget):
    """Статичные страницы: раздел задаётся родительской страницей, её url —
    префикс адресов статей, дубль ловится по совпадению url."""

    kind = "pages"
    slug_limit = SLUG_LIMIT_PAGES

    @property
    def url_prefix(self) -> str:
        return self.site.articles_url_prefix

    def is_synced(self) -> bool:
        return bool(self.site.reference_html and self.site.reference_images
                    and self.site.articles_url_prefix)

    def public_path(self, slug: str) -> str:
        return f"{self.site.articles_url_prefix}{slug}/"

    def _fetch_existing(self) -> list[dict]:
        return [{"id": p.get("id"), "title": p.get("title", ""),
                 "url": p.get("url") or "", "label": ""}
                for p in self.client.list_section_pages(self.site.articles_url_prefix)]

    def taken_addresses(self) -> set[str]:
        return {item["url"] for item in self.list_existing()}

    def reference(self, reference_id: int) -> dict:
        page = self.client.get_page(reference_id)
        return {"html": page.get("text") or page.get("body") or "", "label": ""}

    def create(self, article, *, teaser: str, label: str) -> dict:
        # teaser и label приходят и сюда, но у статичной страницы полей под
        # них нет — вызывающему незачем знать, какой цели что нужно.
        return self.client.create_page(
            title=article.title,
            url=self.public_path(article.slug),
            html=article.body_html,
            parent_id=self.site.articles_parent_id,
            meta_description=article.meta_description,
            meta_keywords=article.meta_keywords,
        )

    def update_text(self, remote_id: int, html: str, *, title: str | None = None,
                    meta_description: str | None = None,
                    meta_keywords: str | None = None) -> None:
        self.client.update_page_text(remote_id, html, title=title,
                                     meta_description=meta_description,
                                     meta_keywords=meta_keywords)

    def set_cover(self, remote_id: int, data: bytes, filename: str) -> None:
        self.client.set_page_cover(remote_id, data, filename)


class ArticlesTarget(PublishTarget):
    """Раздел /api/v1/articles/: адрес собирает движок из слага, родителя нет,
    дубль ловится по слагу (url в списке этот ресурс не отдаёт вообще)."""

    kind = "articles"
    slug_limit = SLUG_LIMIT_ARTICLES
    url_prefix = ARTICLES_URL_PREFIX

    def is_synced(self) -> bool:
        # Без articles_url_prefix, в отличие от pages: у этой ветки префикс —
        # константа в коде, в карточке сайта он остаётся пустым и признаком
        # синхронизации служить не может.
        return bool(self.site.reference_html and self.site.reference_images)

    def public_path(self, slug: str) -> str:
        return f"{ARTICLES_URL_PREFIX}{slug}/"

    def _fetch_existing(self) -> list[dict]:
        return [{"id": a.get("id"), "title": a.get("title", ""),
                 "slug": a.get("slug") or "", "label": a.get("label") or "",
                 "url": self.public_path(a.get("slug") or "")}
                for a in self.client.list_articles()]

    def taken_addresses(self) -> set[str]:
        return {item["url"] for item in self.list_existing()}

    def labels(self) -> list[str]:
        """Рубрики, уже существующие в разделе, в порядке первого появления.
        Из них модель выбирает рубрику новой статьи — своих не придумывает,
        иначе на сайте заводятся почти-дубли («О материалах» и «Про
        материалы»)."""
        seen = []
        for item in self.list_existing():
            label = item.get("label") or ""
            if label and label not in seen:
                seen.append(label)
        return seen

    def fallback_label(self) -> str:
        """Рубрика эталонной статьи, а если её в списке нет — первая из
        списка. Эталон обязателен и сам лежит в этом же разделе, поэтому
        первая ветка срабатывает почти всегда, и рубрика по умолчанию
        получается осмысленной, а не случайной."""
        for item in self.list_existing():
            if item.get("id") == self.site.reference_article_id and item.get("label"):
                return item["label"]
        labels = self.labels()
        return labels[0] if labels else ""

    def reference(self, reference_id: int) -> dict:
        article = self.client.get_article(reference_id)
        return {"html": article.get("body") or "", "label": article.get("label") or ""}

    def create(self, article, *, teaser: str, label: str) -> dict:
        return self.client.create_article(
            title=article.title,
            slug=article.slug,
            html=article.body_html,
            teaser=teaser,
            label=label,
            meta_description=article.meta_description,
            meta_keywords=article.meta_keywords,
        )

    def update_text(self, remote_id: int, html: str, *, title: str | None = None,
                    meta_description: str | None = None,
                    meta_keywords: str | None = None) -> None:
        # Тизер пересобирается вместе с meta_description, из которого он и
        # собран: без этого после перегенерации текста на карточке статьи
        # остался бы анонс от прошлой версии, расходящийся с самой статьёй.
        # Меняется только текст (кнопка «перегенерировать картинки») —
        # meta_description приходит None, и тизер трогать незачем.
        teaser = (None if meta_description is None
                  else build_teaser(meta_description, title or ""))
        self.client.update_article_text(remote_id, html, title=title,
                                        teaser=teaser,
                                        meta_description=meta_description,
                                        meta_keywords=meta_keywords)

    def set_cover(self, remote_id: int, data: bytes, filename: str) -> None:
        self.client.set_article_cover(remote_id, data, filename)


def make_target(site: Site, client=None) -> PublishTarget:
    if site.publish_target == "articles":
        return ArticlesTarget(site, client)
    return PagesTarget(site, client)


def build_teaser(meta_description: str, title: str) -> str:
    """Анонс на карточке статьи. Поле обязательное, отдельного текста под него
    не генерируем (решение владельца): берём meta_description, а если модель
    его не вернула — заголовок, лишь бы не слать пустое. Экранируем как
    HTML-содержимое: meta_description — обычный текст, и амперсанд или угловая
    скобка в нём не должны ломать разметку карточки."""
    text = (meta_description or "").strip() or (title or "").strip()
    return f"<p>{html_module.escape(text, quote=False)}</p>" if text else ""


def pick_label(answer: str, target: ArticlesTarget) -> str:
    """Рубрика из ответа модели, сверенная со списком рубрик раздела.

    Сверка без учёта регистра и обрамляющих кавычек: модель отвечает одной
    строкой свободным текстом, и «"Полезное"» с «полезное» — та же рубрика.
    Не совпало ничего — fallback_label(), а не ответ модели как есть: новая
    рубрика на сайте создаётся одним лишь фактом записи такой строки, и
    опечатка модели навсегда осталась бы в списке рубрик сайта."""
    cleaned = (answer or "").strip().strip('"«»\'').strip()
    for label in target.labels():
        if label.casefold() == cleaned.casefold():
            return label
    return target.fallback_label()


def require_label(label: str) -> str:
    """label обязателен на стороне сайта — пустой не примут. Пустым он
    остаётся только если в разделе нет ни одной статьи, то есть и эталона
    взять неоткуда; текст ошибки говорит про это прямо, а не про HTTP 400,
    который иначе прилетел бы от сайта."""
    if not label:
        raise SiteAPIError(
            "в разделе статей сайта нет ни одной рубрики — опубликуй на сайте "
            "первую статью вручную, чтобы задать рубрику и эталон")
    return label[:LABEL_LIMIT]
