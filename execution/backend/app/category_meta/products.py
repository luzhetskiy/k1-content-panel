"""Названия товаров категории — для SEO-текста.

Фильтр ?category= сайт игнорирует, поэтому читается весь /products-common/
(на stroybaza-moscow.ru 4410 товаров, 89 страниц по 50, ~2 минуты) один раз на
запуск. Товар знает свои категории только по названию; у категории-контейнера
своих товаров нет, поэтому ей достаются товары подкатегорий."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.category_meta import CategoryMeta
from app.sites.client import SiteAPIError

logger = logging.getLogger(__name__)

PRODUCTS_PER_CATEGORY = 25


def _key(name: object) -> str:
    return " ".join(str(name or "").split()).casefold()


def products_by_category(products: list[dict], rows: list[CategoryMeta]) -> dict[int, list[str]]:
    """{id категории: до PRODUCTS_PER_CATEGORY названий опубликованных товаров}."""
    by_category: dict[str, list[str]] = {}
    for product in products:
        name = " ".join(str(product.get("name") or "").split())
        if not name or not product.get("published", True):
            continue
        for category in product.get("categories") or []:
            by_category.setdefault(_key(category), []).append(name)

    children: dict[int, list[CategoryMeta]] = {}
    for row in rows:
        if row.remote_parent_id:
            children.setdefault(row.remote_parent_id, []).append(row)

    result: dict[int, list[str]] = {}
    for row in rows:
        names: list[str] = []
        pending, seen = [row], set()
        while pending and len(names) < PRODUCTS_PER_CATEGORY:
            current = pending.pop(0)
            if current.remote_id in seen:
                continue
            seen.add(current.remote_id)
            for name in by_category.get(_key(current.name), []):
                if name not in names:
                    names.append(name)
            pending += children.get(current.remote_id, [])
        result[row.id] = names[:PRODUCTS_PER_CATEGORY]
    return result


def refresh_product_names(db: Session, site, site_client) -> str:
    """Обновляет product_names_json всех категорий сайта. Сбой чтения товаров
    запуск не останавливает — текст напишется без товаров (или по прошлому
    списку); возвращается текст ошибки для журнала."""
    try:
        products = site_client.list_products()
    except SiteAPIError as exc:
        logger.warning("товары сайта %s не прочитаны: %s", site.id, exc)
        return str(exc)
    rows = list(db.scalars(select(CategoryMeta).where(CategoryMeta.site_id == site.id)))
    names = products_by_category(products, rows)
    for row in rows:
        row.product_names_json = names[row.id]
    db.commit()
    return ""
