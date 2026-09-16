"""Синхронизация дерева категорий каталога сайта в category_meta.

Url категории — по правилу, сверенному 2026-09-16 со всеми 70 категориями из
sitemap stroybaza-moscow.ru: корневая — /catalog/{slug}/, вложенная —
/catalog/category/{slug непосредственного родителя}/{slug}/. Sitemap нужен
только чтобы отсеять категории без живой страницы; недоступен — не отсеиваем.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models.category_meta import CategoryMeta
from app.sites.client import SiteAPIError

SKIP_UNPUBLISHED = "не опубликована"
SKIP_NO_PAGE = "нет страницы на сайте"
SKIP_DELETED = "удалена на сайте"


@dataclass
class SyncResult:
    active: list[CategoryMeta]
    skipped: int
    sitemap_error: str


def category_url(category: dict, by_id: dict[int, dict]) -> str:
    parent_id = category.get("parent")
    if not parent_id:
        return f"/catalog/{category['slug']}/"
    parent = by_id.get(parent_id)
    if parent is None:
        return ""      # родитель не пришёл в выгрузке — url не угадываем
    return f"/catalog/category/{parent['slug']}/{category['slug']}/"


def category_path(category: dict, by_id: dict[int, dict]) -> str:
    names, current, seen = [], category, set()
    while current is not None and current["id"] not in seen:
        seen.add(current["id"])
        names.append(str(current.get("name", "")).strip())
        parent_id = current.get("parent")
        current = by_id.get(parent_id) if parent_id else None
    return " / ".join(reversed(names))


def sync_categories(db: Session, site, site_client) -> SyncResult:
    remote = site_client.list_catalog_categories()
    try:
        sitemap: set[str] | None = site_client.fetch_sitemap_paths()
        sitemap_error = ""
    except SiteAPIError as exc:
        sitemap, sitemap_error = None, str(exc)

    by_id = {int(item["id"]): item for item in remote}
    existing = {row.remote_id: row for row in db.scalars(
        select(CategoryMeta).where(CategoryMeta.site_id == site.id)).all()}
    active: list[CategoryMeta] = []
    skipped = 0
    for remote_id, data in by_id.items():
        row = existing.pop(remote_id, None)
        if row is None:
            row = CategoryMeta(site_id=site.id, remote_id=remote_id)
            db.add(row)
        row.remote_parent_id = data.get("parent") or None
        row.name = str(data.get("name", "")).strip()
        row.slug = data.get("slug") or ""
        row.path = category_path(data, by_id)
        row.url = category_url(data, by_id)
        if not data.get("published"):
            reason = SKIP_UNPUBLISHED
        elif not row.url or (sitemap is not None and row.url not in sitemap):
            reason = SKIP_NO_PAGE
        else:
            reason = ""
        row.skip_reason = reason
        if reason:
            row.status = "skipped"
            skipped += 1
        else:
            if row.status in (None, "skipped"):
                row.status = "new"
            active.append(row)
        row.updated_at = utcnow()
    for row in existing.values():
        row.skip_reason = SKIP_DELETED
        row.status = "skipped"
        row.updated_at = utcnow()
        skipped += 1
    db.commit()
    return SyncResult(active=active, skipped=skipped, sitemap_error=sitemap_error)
