"""API раздела «Проекты домов» (directions/2026-10-18-house-projects-rewrite-design.md).

Проект — карточка сайта с projects_enabled=True; отдельной сущности нет.
Доступ — всем залогиненным, как у «Метатегов категорий»."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin_sites import open_client as open_site_client
from app.api.deps import get_current_user, get_db
from app.clock import utcnow
from app.house_projects.markup import plain_text
from app.house_projects.runs import (
    abort_run, active_run, is_busy, is_lost, is_stale, is_stuck, is_taken, last_run,
    pages_for_mode, run_progress,
)
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.site import Site
from app.models.user import User
from app.settings.crypto import SecretDecryptionError
from app.sites.client import SiteAPIError
from app.tasks import enqueue_rewrite_page, enqueue_rewrite_run

router = APIRouter(prefix="/api/house-projects", tags=["house-projects"])

BUSY_TEXT = "идёт рерайт проекта — дождитесь окончания"


class ProjectIn(BaseModel):
    site_id: int
    parent_id: int


class RunIn(BaseModel):
    mode: Literal["new", "all"]


class RunOut(BaseModel):
    id: int
    mode: str
    total: int
    done: int
    failed: int
    started_at: datetime
    stale: bool


class ProjectOut(BaseModel):
    site_id: int
    name: str
    domain: str
    parent_id: int
    parent_title: str
    pages: int                       # страниц раздела на сайте (без пропавших)
    rewritten: int                   # из них переписаны хотя бы раз
    new_count: int                   # возьмёт режим «Только новые»
    error_count: int                 # с ошибкой или зависшие
    last_rewritten_at: datetime | None
    last_error: str                  # ошибка последнего завершённого запуска
    busy: bool                       # запуск или отдельная страница в работе
    run: RunOut | None


class PageOut(BaseModel):
    id: int
    remote_id: int
    title: str
    url: str
    page_url: str
    status: str
    skip_reason: str
    error_text: str
    stuck: bool                      # зависла в работе или потерялась в очереди
    rewritten_at: datetime | None
    rewrite_count: int
    original_chars: int
    rewritten_chars: int
    updated_at: datetime


class PageDetailOut(PageOut):
    original_html: str
    rewritten_html: str


def _run_alive(db: Session, site: Site) -> bool:
    run = active_run(db, site.id)
    return run is not None and not is_stale(db, run)


def _stuck(row: HouseProjectPage, run_alive: bool) -> bool:
    """Зависла в работе или потерялась в очереди. Во время живого запуска
    страница честно ждёт своей очереди часами — потерянной она не считается."""
    return is_stuck(row) or (is_lost(row) and not run_alive)


def _project_out(db: Session, site: Site) -> ProjectOut:
    run = active_run(db, site.id)
    run_out = None
    if run is not None:
        progress = run_progress(db, run)
        run_out = RunOut(id=run.id, mode=run.mode, total=progress.total, done=progress.done,
                         failed=progress.failed, started_at=run.started_at,
                         stale=progress.stale)
    run_alive = run_out is not None and not run_out.stale
    rows = db.scalars(select(HouseProjectPage).where(
        HouseProjectPage.site_id == site.id, HouseProjectPage.status != "skipped")).all()
    dates = [row.rewritten_at for row in rows if row.rewritten_at is not None]
    finished = last_run(db, site.id)
    return ProjectOut(
        site_id=site.id, name=site.name, domain=site.domain,
        parent_id=site.projects_parent_id, parent_title=site.projects_parent_title,
        pages=len(rows), rewritten=len(dates),
        new_count=len(pages_for_mode(db, site.id, "new")),
        error_count=sum(1 for row in rows if row.status == "failed" or _stuck(row, run_alive)),
        last_rewritten_at=max(dates) if dates else None,
        last_error=finished.error_text if finished else "",
        busy=is_busy(db, site.id), run=run_out)


def _page_out(site: Site, row: HouseProjectPage, run_alive: bool) -> dict:
    return dict(
        id=row.id, remote_id=row.remote_id, title=row.title, url=row.url,
        page_url=f"{site.base_url.rstrip('/')}{row.url}" if row.url else "",
        status=row.status, skip_reason=row.skip_reason, error_text=row.error_text,
        stuck=_stuck(row, run_alive), rewritten_at=row.rewritten_at,
        rewrite_count=row.rewrite_count,
        original_chars=len(plain_text(row.original_html)),
        rewritten_chars=len(plain_text(row.rewritten_html)), updated_at=row.updated_at)


def _project_or_404(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None or not site.projects_enabled:
        raise HTTPException(404, "проект не найден")
    return site


def _parent_title(db: Session, site: Site, parent_id: int) -> str:
    """Родительская страница должна быть на сайте — её название менеджер видит
    в списке проектов и по нему сверяет, что не ошибся с id."""
    try:
        detail = open_site_client(db, site).get_page(parent_id)
    except SecretDecryptionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SiteAPIError as exc:
        if exc.status_code == 404:
            raise HTTPException(400, f"страница {parent_id} не найдена на сайте") from exc
        raise HTTPException(502, f"сайт не ответил: {exc}") from exc
    return (detail.get("title") or f"страница {parent_id}")[:300]


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    sites = db.scalars(select(Site).where(Site.projects_enabled.is_(True))
                       .order_by(Site.name)).all()
    return [_project_out(db, site) for site in sites]


@router.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectIn, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    site = db.get(Site, payload.site_id)
    if site is None:
        raise HTTPException(404, "сайт не найден")
    if site.projects_enabled:
        raise HTTPException(400, "проект для этого сайта уже добавлен")
    site.projects_parent_title = _parent_title(db, site, payload.parent_id)
    site.projects_parent_id = payload.parent_id
    site.projects_enabled = True
    db.commit()
    return _project_out(db, site)


@router.put("/projects/{site_id}", response_model=ProjectOut)
def update_project(site_id: int, payload: ProjectIn, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    """Смена родительской страницы. Страницы прежнего родителя следующая
    синхронизация пометит «удалена на сайте»."""
    site = _project_or_404(db, site_id)
    if is_busy(db, site.id):
        raise HTTPException(409, BUSY_TEXT)
    site.projects_parent_title = _parent_title(db, site, payload.parent_id)
    site.projects_parent_id = payload.parent_id
    db.commit()
    return _project_out(db, site)


@router.delete("/projects/{site_id}")
def delete_project(site_id: int, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    """Убирает сайт из раздела; страницы и даты рерайта в панели остаются."""
    site = _project_or_404(db, site_id)
    if is_busy(db, site.id):
        raise HTTPException(409, BUSY_TEXT)
    site.projects_enabled = False
    db.commit()
    return {"ok": True}


@router.post("/projects/{site_id}/run", response_model=ProjectOut)
def run_project(site_id: int, payload: RunIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    if is_busy(db, site.id):
        raise HTTPException(409, "рерайт уже идёт")
    current = active_run(db, site.id)
    if current is not None and is_stale(db, current):
        abort_run(db, current, "прерван: цепочка задач оборвалась, рерайт запущен заново")
    run = ProjectRewriteRun(site_id=site.id, mode=payload.mode, created_by_id=user.id,
                            started_at=utcnow())
    db.add(run)
    db.commit()
    enqueue_rewrite_run(run.id)
    return _project_out(db, site)


@router.get("/projects/{site_id}/pages", response_model=list[PageOut])
def list_pages(site_id: int, db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    rows = db.scalars(select(HouseProjectPage).where(HouseProjectPage.site_id == site.id)
                      .order_by(HouseProjectPage.title, HouseProjectPage.id)).all()
    run_alive = _run_alive(db, site)
    return [_page_out(site, row, run_alive) for row in rows]


def _page_or_404(db: Session, page_id: int) -> tuple[HouseProjectPage, Site]:
    row = db.get(HouseProjectPage, page_id)
    if row is None:
        raise HTTPException(404, "страница не найдена")
    return row, _project_or_404(db, row.site_id)


@router.get("/pages/{page_id}", response_model=PageDetailOut)
def get_page(page_id: int, db: Session = Depends(get_db),
             _user: User = Depends(get_current_user)):
    row, site = _page_or_404(db, page_id)
    return PageDetailOut(**_page_out(site, row, _run_alive(db, site)),
                         original_html=row.original_html,
                         rewritten_html=row.rewritten_html)


@router.post("/pages/{page_id}/rewrite", response_model=PageOut)
def rewrite_one(page_id: int, db: Session = Depends(get_db),
                _user: User = Depends(get_current_user)):
    row, site = _page_or_404(db, page_id)
    if row.status == "skipped":
        raise HTTPException(400, f"страница пропущена: {row.skip_reason}")
    run = active_run(db, site.id)
    if run is not None:
        if not is_stale(db, run):
            raise HTTPException(409, BUSY_TEXT)
        # Оборванный запуск закрываем, как перед новым запуском: иначе
        # переписанная страница своим updated_at «оживила» бы его, и проект
        # стоял бы занятым ещё два часа без единой работающей задачи.
        abort_run(db, run, "прерван: цепочка задач оборвалась, страница переписана отдельно")
    if is_taken(row):
        raise HTTPException(409, "страница уже в работе")
    row.status, row.error_text, row.started_at = "queued", "", None
    row.updated_at = utcnow()
    db.commit()
    enqueue_rewrite_page(row.id, continue_run=False)
    return _page_out(site, row, False)
