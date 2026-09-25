"""Разовое дописывание SEO-текстов к категориям, у которых теги уже готовы.

Без аргументов — только показывает, что будет сделано:
    python -m app.category_meta.backfill_seo_text
Запуск по сайтам (id из списка) или по всем:
    python -m app.category_meta.backfill_seo_text --start 3 5
    python -m app.category_meta.backfill_seo_text --start all

Каждый сайт — обычный запуск в режиме seo_text: прогресс виден в панели,
ожидание квоты и один слот воркера — как у «Обновить метатеги». Теги не
пересчитываются, на сайт уходит только seo_text метатега."""

from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin_sites import open_client
from app.category_meta.products import refresh_product_names
from app.category_meta.runs import active_run, open_partial_run
from app.db import SessionLocal
from app.models.category_meta import CategoryMeta
from app.models.site import Site
from app.seed import seed_prompts
from app.tasks import enqueue_category_meta


def ready_categories(db: Session, site_id: int) -> list[CategoryMeta]:
    return list(db.scalars(select(CategoryMeta).where(
        CategoryMeta.site_id == site_id, CategoryMeta.status == "done",
        CategoryMeta.title != "", CategoryMeta.seed_phrase != "").order_by(CategoryMeta.id)))


def start_site(db: Session, site: Site) -> str:
    if active_run(db, site.id) is not None:
        return "пропущен: у сайта незавершённый запуск метатегов"
    rows = ready_categories(db, site.id)
    if not rows:
        return "пропущен: нет категорий с готовыми тегами"
    error = refresh_product_names(db, site, open_client(db, site))
    open_partial_run(db, site.id, rows, None, mode="seo_text")
    enqueue_category_meta(rows[0].id)
    return f"запущен: {len(rows)} категорий" + (f" (товары не прочитаны: {error})" if error else "")


def main(argv: list[str]) -> None:
    db = SessionLocal()
    try:
        seed_prompts(db)
        sites = list(db.scalars(select(Site).where(Site.meta_enabled.is_(True)).order_by(Site.id)))
        if not argv:
            for site in sites:
                print(f"{site.id}\t{site.name}\tготовых категорий: "
                      f"{len(ready_categories(db, site.id))}")
            return
        if argv[0] != "--start":
            sys.exit(__doc__)
        wanted = None if argv[1:] == ["all"] else {int(item) for item in argv[1:]}
        for site in sites:
            if wanted is None or site.id in wanted:
                print(f"{site.id}\t{site.name}\t{start_site(db, site)}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
