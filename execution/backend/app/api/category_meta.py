"""API раздела «Метатеги категорий» (directions/2026-09-16-category-meta-design.md).

Проект — это карточка сайта с meta_enabled=True; отдельной сущности нет.
Доступ — всем залогиненным, как у «Статей» и «Строителей»."""

from __future__ import annotations

import math
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.factory import AIConfigError, build_text_client
from app.ai.text import LLMError
from app.api.deps import get_current_user, get_db
from app.category_meta.city import CityFormError, city_in_for
from app.category_meta.runs import (
    active_run, is_stale, is_stuck, last_run, last_successful_run, run_progress,
)
from app.clock import utcnow
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.job import JobRun
from app.models.site import Site
from app.models.user import User
from app.tasks import enqueue_category_meta, enqueue_meta_run
from app.wordstat.client import WordstatError
from app.wordstat.factory import WordstatConfigError, build_wordstat_client, hourly_limit
from app.wordstat.quota import QuotaExceeded
from app.wordstat.regions import load_regions, search_regions

router = APIRouter(prefix="/api/category-meta", tags=["category-meta"])


class ProjectIn(BaseModel):
    site_id: int
    city: str
    city_in: str = ""        # пусто — определит LLM
    brand: str
    wordstat_region_id: int


class RunOut(BaseModel):
    id: int
    total: int
    done: int
    failed: int
    started_at: datetime
    wait_until: datetime | None
    stale: bool


class ProjectOut(BaseModel):
    site_id: int
    name: str
    domain: str
    base_url: str
    city: str
    city_in: str
    brand: str
    wordstat_region_id: int | None
    updated_at: datetime | None      # finished_at последнего успешного запуска
    last_error: str                  # ошибка последнего завершённого запуска, если была
    run: RunOut | None


class RegionOut(BaseModel):
    id: int
    label: str
    path: str


class CandidateOut(BaseModel):
    phrase: str
    count: int | None


class CategoryOut(BaseModel):
    id: int
    remote_id: int
    remote_parent_id: int | None
    name: str
    path: str
    url: str
    page_url: str
    status: str
    skip_reason: str
    error_text: str
    stuck: bool
    seed_phrase: str
    candidates: list[CandidateOut]
    form_nominative: str
    form_buy: str
    chosen_form: str
    nominative_count: int | None
    declined_count: int | None
    total_count: int | None
    low_demand: bool
    title: str
    h1: str
    meta_description: str
    meta_keywords: str
    ai_keywords: str
    previous_json: dict | None
    wait_until: datetime | None
    updated_at: datetime


def _project_out(db: Session, site: Site) -> ProjectOut:
    run = active_run(db, site.id)
    run_out = None
    if run is not None:
        progress = run_progress(db, run)
        run_out = RunOut(id=run.id, total=progress.total, done=progress.done,
                         failed=progress.failed, started_at=run.started_at,
                         wait_until=progress.wait_until, stale=progress.stale)
    finished = last_run(db, site.id)
    successful = last_successful_run(db, site.id)
    return ProjectOut(
        site_id=site.id, name=site.name, domain=site.domain, base_url=site.base_url,
        city=site.city, city_in=site.city_in, brand=site.brand,
        wordstat_region_id=site.wordstat_region_id,
        updated_at=successful.finished_at if successful else None,
        last_error=finished.error_text if finished else "", run=run_out)


def _category_out(site: Site, row: CategoryMeta) -> CategoryOut:
    return CategoryOut(
        id=row.id, remote_id=row.remote_id, remote_parent_id=row.remote_parent_id,
        name=row.name, path=row.path, url=row.url,
        page_url=f"{site.base_url.rstrip('/')}{row.url}" if row.url else "",
        status=row.status, skip_reason=row.skip_reason, error_text=row.error_text,
        stuck=is_stuck(row), seed_phrase=row.seed_phrase,
        candidates=[CandidateOut(phrase=v.get("phrase", ""), count=v.get("count"))
                    for v in (row.candidates_json or []) if isinstance(v, dict)],
        form_nominative=row.form_nominative,
        form_buy=row.form_buy, chosen_form=row.chosen_form,
        nominative_count=row.nominative_count, declined_count=row.declined_count,
        total_count=row.total_count, low_demand=row.low_demand, title=row.title, h1=row.h1,
        meta_description=row.meta_description, meta_keywords=row.meta_keywords,
        ai_keywords=row.ai_keywords, previous_json=row.previous_json,
        wait_until=row.wait_until, updated_at=row.updated_at)


def _project_or_404(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None or not site.meta_enabled:
        raise HTTPException(404, "проект не найден")
    return site


def _apply_project(db: Session, site: Site, payload: ProjectIn) -> None:
    city, brand = payload.city.strip(), payload.brand.strip()
    if not city or not brand:
        raise HTTPException(400, "укажите город и бренд")
    city_in = " ".join(payload.city_in.split())
    # Город сменили, а поле «с предлогом» осталось прежним — оно от старого города.
    if not city_in or (city != site.city and city_in == site.city_in):
        try:
            city_in = city_in_for(build_text_client(db, max_retries=1), city)
        except AIConfigError as exc:
            raise HTTPException(400, f"{exc} — или впишите город с предлогом вручную") from exc
        except (LLMError, CityFormError) as exc:
            raise HTTPException(502, f"не удалось определить город с предлогом: {exc} — "
                                     f"впишите его вручную") from exc
    site.city, site.city_in, site.brand = city, city_in, brand
    site.wordstat_region_id = payload.wordstat_region_id
    site.meta_enabled = True


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    sites = db.scalars(select(Site).where(Site.meta_enabled.is_(True)).order_by(Site.name)).all()
    return [_project_out(db, site) for site in sites]


@router.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectIn, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    site = db.get(Site, payload.site_id)
    if site is None:
        raise HTTPException(404, "сайт не найден")
    if site.meta_enabled:
        raise HTTPException(400, "проект для этого сайта уже добавлен")
    _apply_project(db, site, payload)
    db.commit()
    return _project_out(db, site)


@router.put("/projects/{site_id}", response_model=ProjectOut)
def update_project(site_id: int, payload: ProjectIn, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    _apply_project(db, site, payload)
    db.commit()
    return _project_out(db, site)


@router.delete("/projects/{site_id}")
def delete_project(site_id: int, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    """Убирает сайт из раздела; категории и теги в панели остаются —
    на сайте они и так остаются."""
    site = _project_or_404(db, site_id)
    run = active_run(db, site.id)
    if run is not None and not is_stale(db, run):
        raise HTTPException(409, "идёт обновление метатегов — дождитесь окончания")
    site.meta_enabled = False
    db.commit()
    return {"ok": True}


@router.get("/regions", response_model=list[RegionOut])
def regions(q: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    try:
        found = load_regions(db, hourly_limit(db), lambda: build_wordstat_client(db))
    except WordstatConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except QuotaExceeded as exc:
        raise HTTPException(429, f"квота Wordstat на этот час исчерпана — повторите через "
                                 f"{math.ceil(exc.seconds / 60)} мин") from exc
    except WordstatError as exc:
        raise HTTPException(502, str(exc)) from exc
    return [RegionOut(id=r.id, label=r.label, path=r.path) for r in search_regions(found, q)]


@router.post("/projects/{site_id}/run", response_model=ProjectOut)
def run_project(site_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    if not (site.city_in and site.brand and site.wordstat_region_id):
        raise HTTPException(400, "заполните у проекта город, бренд и регион Wordstat")
    current = active_run(db, site.id)
    if current is not None:
        if not is_stale(db, current):
            raise HTTPException(409, "обновление метатегов уже идёт")
        current.error_text = "прерван: цепочка задач оборвалась, обновление запущено заново"
        current.finished_at = utcnow()
        job = db.get(JobRun, current.job_run_id) if current.job_run_id else None
        if job is not None and job.finished_at is None:
            job.status, job.log_text, job.finished_at = "failed", current.error_text, utcnow()
    run = MetaRun(site_id=site.id, created_by_id=user.id)
    db.add(run)
    db.commit()
    enqueue_meta_run(run.id)
    return _project_out(db, site)


@router.get("/projects/{site_id}/categories", response_model=list[CategoryOut])
def list_categories(site_id: int, db: Session = Depends(get_db),
                    _user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    rows = db.scalars(select(CategoryMeta).where(CategoryMeta.site_id == site.id)
                      .order_by(CategoryMeta.path, CategoryMeta.id)).all()
    return [_category_out(site, row) for row in rows]


@router.post("/categories/{category_id}/regenerate", response_model=CategoryOut)
def regenerate_category(category_id: int, db: Session = Depends(get_db),
                        _user: User = Depends(get_current_user)):
    row = db.get(CategoryMeta, category_id)
    if row is None:
        raise HTTPException(404, "категория не найдена")
    site = _project_or_404(db, row.site_id)
    if row.status == "skipped":
        raise HTTPException(400, f"категория пропущена: {row.skip_reason}")
    if not row.seed_phrase:
        raise HTTPException(400, "у категории ещё нет поисковой фразы — сначала обновите "
                                 "метатеги проекта целиком")
    if row.status == "queued" or (row.status == "in_work" and not is_stuck(row)):
        raise HTTPException(409, "категория уже в работе")
    row.status, row.error_text, row.wait_until = "queued", "", None
    row.updated_at = utcnow()
    db.commit()
    enqueue_category_meta(row.id, continue_run=False)
    return _category_out(site, row)
