"""Запись тегов категории в /api/v1/metatags/ сайта.

Есть метатег с этим url — PATCH пяти полей, нет — POST. seo_text не трогаем.
POST вслепую не повторяется: ReadTimeout после отправки неотличим от «создано,
ответ не дошёл». Поэтому каждая попытка заново читает список метатегов — если
предыдущий POST на деле прошёл, следующая попытка найдёт запись и сделает PATCH
(тот же приём, что _guard_duplicate_url у статей)."""

from __future__ import annotations

import time

from app.models.category_meta import CategoryMeta
from app.sites.client import SiteAPIError

PUBLISHED_FIELDS = ("title", "h1", "meta_description", "meta_keywords", "ai_keywords")
MAX_ATTEMPTS = 3


def _retryable(exc: SiteAPIError) -> bool:
    return exc.status_code is None or exc.status_code >= 500


def _find(site_client, url: str) -> dict | None:
    for item in site_client.list_metatags():
        if item.get("url") == url:
            return item
    return None


def publish_metatag(site_client, category: CategoryMeta, tags: dict, sleep=time.sleep) -> None:
    fields = {name: tags[name] for name in PUBLISHED_FIELDS}
    for attempt in range(MAX_ATTEMPTS):
        try:
            existing = _find(site_client, category.url)
            if existing is not None:
                if category.previous_json is None:
                    category.previous_json = {name: existing.get(name) or ""
                                              for name in PUBLISHED_FIELDS}
                site_client.update_metatag(existing["id"], fields)
                category.remote_metatag_id = existing["id"]
            else:
                if category.previous_json is None:
                    category.previous_json = {}
                created = site_client.create_metatag(category.url, fields)
                category.remote_metatag_id = created["id"]
            return
        except SiteAPIError as exc:
            if not _retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            sleep(2 ** attempt)
