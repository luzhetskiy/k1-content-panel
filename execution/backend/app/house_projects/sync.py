"""Синхронизация страниц раздела «Проекты домов» с сайтом.

Список staticpages не отдаёт `parent`, фильтр `?parent=` сайт игнорирует, а url
детей (`/pr/...`) не совпадает с url родителя (`/addresses/projects/`) — поэтому
читается карточка каждой страницы сайта: на stroybaza-samara.ru 265 запросов,
1–2 минуты. Функция не коммитит: при сбое посреди чтения вызывающий откатывает
всё разом, и статусы страниц не меняются."""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models.house_project import HouseProjectPage
from app.sites.client import SiteAPIError

NOT_ON_SITE = "удалена на сайте"
MAX_ATTEMPTS = 3


def with_retries(call, sleep=time.sleep):
    """Сеть и 5xx сайта — до трёх попыток. Только для чтения и PATCH: их
    повторять безопасно, дублей они не создают."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            return call()
        except SiteAPIError as exc:
            retryable = exc.status_code is None or exc.status_code >= 500
            if not retryable or attempt == MAX_ATTEMPTS - 1:
                raise
            sleep(2 ** attempt)


def sync_pages(db: Session, site, site_client, sleep=time.sleep) -> list[HouseProjectPage]:
    """Прямые дети projects_parent_id — в панель; пропавшие — «удалена на сайте».
    Возвращает страницы, которые сейчас есть на сайте."""
    listed = with_retries(lambda: site_client.list_section_pages(""), sleep)
    children, read = [], set()
    for item in listed:
        page_id = item.get("id")
        # Страница может прийти дважды: выдача постраничная, и добавление
        # страницы на сайте во время чтения сдвигает её.
        if page_id == site.projects_parent_id or page_id in read:
            continue
        read.add(page_id)
        try:
            detail = with_retries(lambda: site_client.get_page(page_id), sleep)
        except SiteAPIError as exc:
            if exc.status_code == 404:
                continue   # удалили, пока читали список — станет «удалена на сайте»
            raise
        if detail.get("parent") == site.projects_parent_id:
            children.append(detail)

    existing = {row.remote_id: row for row in db.scalars(
        select(HouseProjectPage).where(HouseProjectPage.site_id == site.id)).all()}
    now = utcnow()
    present = []
    for detail in children:
        row = existing.get(detail["id"])
        if row is None:
            row = HouseProjectPage(site_id=site.id, remote_id=detail["id"], status="new",
                                   updated_at=now)
            db.add(row)
        row.title = (detail.get("title") or "")[:300]
        row.url = (detail.get("url") or "")[:500]
        if row.status == "skipped":
            # Вернулась на сайт. Блок описания задача страницы проверит заново,
            # когда возьмёт её в работу: «Все заново» — всегда, «Только
            # новые» — если страница ещё не переписывалась.
            row.status = "done" if row.rewritten_at else "new"
            row.skip_reason = ""
            row.updated_at = now
        present.append(row)
    seen = {detail["id"] for detail in children}
    for remote_id, row in existing.items():
        if remote_id not in seen and row.skip_reason != NOT_ON_SITE:
            row.status, row.skip_reason, row.updated_at = "skipped", NOT_ON_SITE, now
    db.flush()
    return sorted(present, key=lambda row: row.remote_id)
