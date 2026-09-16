"""Состояние запуска «Обновить метатеги»: прогресс, зависшие категории,
оборванная цепочка задач."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import as_utc, utcnow
from app.models.category_meta import CategoryMeta, MetaRun

ACTIVE_STATUSES = ("queued", "in_work")
# Мягкий лимит задачи категории — 2300 с ≈ 38 минут (CATEGORY_SOFT_LIMIT в
# app/tasks.py); раньше него «зависла» — ложная тревога.
STUCK_AFTER = timedelta(minutes=40)
# Цепочка может честно стоять в очереди Celery за партией статей часами — поэтому
# «оборвалась» только после двух часов без единого признака жизни.
STALE_AFTER = timedelta(hours=2)


@dataclass
class RunProgress:
    total: int
    done: int
    failed: int
    wait_until: datetime | None
    stale: bool


def is_stuck(category: CategoryMeta, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return (category.status == "in_work" and category.started_at is not None
            and as_utc(category.started_at) < now - STUCK_AFTER)


def active_run(db: Session, site_id: int) -> MetaRun | None:
    return db.scalars(select(MetaRun).where(MetaRun.site_id == site_id,
                                            MetaRun.finished_at.is_(None))
                      .order_by(MetaRun.id.desc())).first()


def last_run(db: Session, site_id: int) -> MetaRun | None:
    return db.scalars(select(MetaRun).where(MetaRun.site_id == site_id,
                                            MetaRun.finished_at.is_not(None))
                      .order_by(MetaRun.finished_at.desc())).first()


def last_successful_run(db: Session, site_id: int) -> MetaRun | None:
    return db.scalars(select(MetaRun).where(MetaRun.site_id == site_id,
                                            MetaRun.finished_at.is_not(None),
                                            MetaRun.error_text == "")
                      .order_by(MetaRun.finished_at.desc())).first()


def next_queued(db: Session, site_id: int) -> CategoryMeta | None:
    return db.scalars(select(CategoryMeta).where(CategoryMeta.site_id == site_id,
                                                 CategoryMeta.status == "queued")
                      .order_by(CategoryMeta.id)).first()


def _active_categories(db: Session, site_id: int) -> list[CategoryMeta]:
    return list(db.scalars(select(CategoryMeta).where(
        CategoryMeta.site_id == site_id, CategoryMeta.status.in_(ACTIVE_STATUSES))).all())


def is_stale(db: Session, run: MetaRun, now: datetime | None = None) -> bool:
    if run.finished_at is not None:
        return False
    now = now or utcnow()
    categories = _active_categories(db, run.site_id)
    for category in categories:
        if category.status == "in_work" and not is_stuck(category, now):
            return False
        if (category.status == "queued" and category.wait_until is not None
                and as_utc(category.wait_until) > now - STALE_AFTER):
            return False
    moments = [as_utc(run.started_at)] + [as_utc(c.updated_at) for c in categories if c.updated_at]
    return max(moments) < now - STALE_AFTER


def run_progress(db: Session, run: MetaRun, now: datetime | None = None) -> RunProgress:
    now = now or utcnow()

    def count(status: str) -> int:
        return db.scalar(select(func.count()).select_from(CategoryMeta).where(
            CategoryMeta.site_id == run.site_id, CategoryMeta.status == status)) or 0

    waits = [as_utc(c.wait_until) for c in _active_categories(db, run.site_id)
             if c.status == "queued" and c.wait_until is not None and as_utc(c.wait_until) > now]
    return RunProgress(total=run.total, done=count("done"), failed=count("failed"),
                       wait_until=min(waits) if waits else None, stale=is_stale(db, run, now))


def finish_run_if_complete(db: Session, site_id: int, now: datetime | None = None) -> MetaRun | None:
    """Закрывает открытый запуск, если у сайта не осталось queued/in_work.
    Возвращает закрытый запуск или None."""
    run = active_run(db, site_id)
    if run is None or _active_categories(db, site_id):
        return None
    run.finished_at = now or utcnow()
    db.commit()
    return run
