"""Состояние запуска «Запустить рерайт»: отбор страниц, прогресс, зависшие
страницы, оборванная цепочка задач."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc, utcnow
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.job import JobRun

ACTIVE_STATUSES = ("queued", "in_work")
# Жёсткий лимит задачи страницы — 1560 с = 26 минут (PAGE_HARD_LIMIT в
# app/tasks.py); раньше него «зависла» — ложная тревога.
STUCK_AFTER = timedelta(minutes=30)
# Цепочка может честно стоять в очереди Celery за партией статей — поэтому
# «оборвалась» только после двух часов без единого признака жизни.
STALE_AFTER = timedelta(hours=2)


@dataclass
class RunProgress:
    total: int
    done: int
    failed: int
    stale: bool


def is_stuck(page: HouseProjectPage, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return (page.status == "in_work" and page.started_at is not None
            and as_utc(page.started_at) < now - STUCK_AFTER)


def is_lost(page: HouseProjectPage, now: datetime | None = None) -> bool:
    """В очереди без признаков жизни два часа — сообщение Celery потеряно."""
    now = now or utcnow()
    return (page.status == "queued" and page.updated_at is not None
            and as_utc(page.updated_at) < now - STALE_AFTER)


def is_taken(page: HouseProjectPage, now: datetime | None = None) -> bool:
    """Страницу сейчас обрабатывает или ждёт живая задача: в очереди или в работе,
    не зависла и не потерялась."""
    return (page.status in ACTIVE_STATUSES and not is_stuck(page, now)
            and not is_lost(page, now))


def active_run(db: Session, site_id: int) -> ProjectRewriteRun | None:
    return db.scalars(select(ProjectRewriteRun).where(
        ProjectRewriteRun.site_id == site_id, ProjectRewriteRun.finished_at.is_(None))
        .order_by(ProjectRewriteRun.id.desc())).first()


def last_run(db: Session, site_id: int) -> ProjectRewriteRun | None:
    return db.scalars(select(ProjectRewriteRun).where(
        ProjectRewriteRun.site_id == site_id, ProjectRewriteRun.finished_at.is_not(None))
        .order_by(ProjectRewriteRun.finished_at.desc())).first()


def next_queued(db: Session, site_id: int) -> HouseProjectPage | None:
    return db.scalars(select(HouseProjectPage).where(
        HouseProjectPage.site_id == site_id, HouseProjectPage.status == "queued")
        .order_by(HouseProjectPage.remote_id)).first()


def active_pages(db: Session, site_id: int) -> list[HouseProjectPage]:
    return list(db.scalars(select(HouseProjectPage).where(
        HouseProjectPage.site_id == site_id,
        HouseProjectPage.status.in_(ACTIVE_STATUSES))).all())


def is_stale(db: Session, run: ProjectRewriteRun, now: datetime | None = None) -> bool:
    if run.finished_at is not None:
        return False
    now = now or utcnow()
    if any(page.status == "in_work" and not is_stuck(page, now)
           for page in active_pages(db, run.site_id)):
        return False
    # Признак жизни — последнее изменение ЛЮБОЙ страницы сайта, а не только
    # стоящих в очереди: только что переписанная страница из очереди уже ушла,
    # а следующая честно ждёт слот воркера за партией статей.
    pages = db.scalars(select(HouseProjectPage).where(
        HouseProjectPage.site_id == run.site_id)).all()
    moments = [as_utc(run.started_at)] + [as_utc(p.updated_at) for p in pages if p.updated_at]
    return max(moments) < now - STALE_AFTER


def is_busy(db: Session, site_id: int, now: datetime | None = None) -> bool:
    """Идёт запуск или переписывается отдельная страница. Зависшая и потерянная
    страница проект не держат."""
    now = now or utcnow()
    run = active_run(db, site_id)
    if run is not None and not is_stale(db, run, now):
        return True
    return any(is_taken(page, now) for page in active_pages(db, site_id))


def run_counts(db: Session, run: ProjectRewriteRun) -> tuple[int, int]:
    """(готово, ошибок) в пределах запуска: ошибкой считается страница, упавшая
    после его старта; «готово» — остаток (в том числе пропущенные без блока)."""
    started = as_utc(run.started_at)
    failed = sum(1 for page in db.scalars(select(HouseProjectPage).where(
                     HouseProjectPage.site_id == run.site_id,
                     HouseProjectPage.status == "failed"))
                 if page.updated_at is not None and as_utc(page.updated_at) >= started)
    active = len(active_pages(db, run.site_id))
    return max(run.total - active - failed, 0), failed


def run_progress(db: Session, run: ProjectRewriteRun, now: datetime | None = None) -> RunProgress:
    done, failed = run_counts(db, run)
    return RunProgress(total=run.total, done=done, failed=failed, stale=is_stale(db, run, now))


def pages_for_mode(db: Session, site_id: int, mode: str,
                   now: datetime | None = None) -> list[HouseProjectPage]:
    """new — ещё не переписанные, с ошибкой, зависшие и потерянные; all — все,
    кроме пропущенных. Страницу, которую сейчас обрабатывает живая задача, не
    берёт ни один режим — даже если запуск начат в обход проверки занятости."""
    now = now or utcnow()
    rows = [row for row in db.scalars(select(HouseProjectPage).where(
        HouseProjectPage.site_id == site_id, HouseProjectPage.status != "skipped")
        .order_by(HouseProjectPage.remote_id)).all() if not is_taken(row, now)]
    if mode == "all":
        return rows
    return [row for row in rows
            if row.status in ("new", "failed") or is_stuck(row, now) or is_lost(row, now)]


def release_queued(db: Session, site_id: int, now: datetime | None = None) -> None:
    """Страницы оборванного запуска выходят из очереди в прежнее состояние."""
    now = now or utcnow()
    for page in db.scalars(select(HouseProjectPage).where(
            HouseProjectPage.site_id == site_id, HouseProjectPage.status == "queued")).all():
        page.status = "done" if page.rewritten_at else "new"
        page.updated_at = now


def abort_run(db: Session, run: ProjectRewriteRun, text: str) -> None:
    """Закрывает оборвавшийся запуск с пометкой и журнал его расходов."""
    now = utcnow()
    run.error_text, run.finished_at = text, now
    release_queued(db, run.site_id, now)
    job = db.get(JobRun, run.job_run_id) if run.job_run_id else None
    if job is not None and job.finished_at is None:
        job.status, job.log_text, job.finished_at = "failed", text, now
    db.commit()


def finish_run_if_complete(db: Session, site_id: int,
                           now: datetime | None = None) -> ProjectRewriteRun | None:
    """Закрывает открытый запуск, если у сайта не осталось queued/in_work.
    total == 0 — запуск ещё читает страницы с сайта: закрыть его могла бы чужая
    задача страницы, закончившая работу в это время. Запуск, которому нечего
    делать, закрывает сам старт запуска."""
    run = active_run(db, site_id)
    if run is None or run.total == 0 or active_pages(db, site_id):
        return None
    run.finished_at = now or utcnow()
    db.commit()
    return run
