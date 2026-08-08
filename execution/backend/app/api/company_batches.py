"""API партий строителей: создание с автоотбором кандидатов, просмотр,
вычёркивание компании из партии, добор следующей по рейтингу, запуск партии
и повтор одной компании (диспетчеризация Celery, Task 14).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.companies.selection import add_next_candidate, select_candidates
from app.models.company import Company, CompanyBatch, CompanyCandidate, CompanyInfo
from app.models.site import Site
from app.models.user import User
from app.tasks import retry_company, run_company_batch

router = APIRouter(prefix="/api", tags=["companies"])


class BatchIn(BaseModel):
    site_id: int
    region_raw: str
    category_raw: str
    category_normalized: str
    teaser_category_id: int
    teaser_city_id: int
    teaser_location_id: int
    count: int = Field(ge=1, le=50)


class CompanyOut(BaseModel):
    id: int
    name: str
    website: str
    region: str
    rating: float | None
    reviews_count: int
    status: str
    remote_url: str
    error_text: str


class BatchOut(BaseModel):
    id: int
    site_id: int | None
    site_name: str
    region_raw: str
    category_raw: str
    category_normalized: str
    requested_count: int
    status: str
    error_text: str
    created_at: datetime
    companies: list[CompanyOut] = []


def _to_out(db: Session, batch: CompanyBatch) -> BatchOut:
    site = db.get(Site, batch.site_id) if batch.site_id is not None else None
    return BatchOut(
        id=batch.id, site_id=batch.site_id, site_name=site.name if site else "—",
        region_raw=batch.region_raw, category_raw=batch.category_raw,
        category_normalized=batch.category_normalized,
        requested_count=batch.requested_count, status=batch.status,
        error_text=batch.error_text, created_at=batch.created_at,
        companies=[CompanyOut(id=c.id, name=c.name, website=c.website, region=c.region,
                              rating=c.rating, reviews_count=c.reviews_count,
                              status=c.status, remote_url=c.remote_url,
                              error_text=c.error_text)
                  for c in batch.companies],
    )


def _get_or_404(db: Session, batch_id: int) -> CompanyBatch:
    batch = db.get(CompanyBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "партия не найдена")
    return batch


def _company_from_candidate(batch: CompanyBatch, candidate: CompanyCandidate) -> Company:
    return Company(
        site_id=batch.site_id, batch_id=batch.id, candidate_id=candidate.id,
        site_key=candidate.site_key, website=candidate.website_raw, name=candidate.name,
        region=candidate.region_raw, category_normalized=batch.category_normalized,
        rating=candidate.rating, reviews_count=candidate.reviews_count,
        yandex_url=candidate.yandex_url,
    )


def _company_info_from_candidate(company: Company, candidate: CompanyCandidate) -> CompanyInfo:
    """CompanyInfo с достоверными фактами из выгрузки Яндекс.Карт — это то,
    что билдер (app/companies/builder.py) считает YANDEX_INFO_FIELDS и никогда
    не даёт RouterAI переписывать."""
    contact = {
        "address": candidate.address,
        "phone_tel": candidate.phone,
        "phone_text": candidate.phone,
        "email": candidate.email,
        "site_url": candidate.website_raw,
        "site_text": candidate.site_key,
    }
    coordinates = (f"{candidate.lat:.6f}, {candidate.lon:.6f}"
                  if candidate.lat is not None and candidate.lon is not None else "")
    return CompanyInfo(
        company_id=company.id,
        builder_name=candidate.name,
        city_name=candidate.city,
        contacts=[contact] if any(contact.values()) else [],
        address=candidate.address,
        coordinates=coordinates,
    )


def _add_company_from_candidate(db: Session, batch: CompanyBatch, candidate: CompanyCandidate) -> Company:
    company = _company_from_candidate(batch, candidate)
    db.add(company)
    db.flush()
    db.add(_company_info_from_candidate(company, candidate))
    return company


@router.post("/company-batches", response_model=BatchOut)
def create_batch(payload: BatchIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    site = db.get(Site, payload.site_id)
    if site is None:
        raise HTTPException(404, "сайт не найден")
    if not site.is_active:
        raise HTTPException(400, "сайт деактивирован — создание партий недоступно")

    batch = CompanyBatch(
        site_id=payload.site_id, region_raw=payload.region_raw,
        category_raw=payload.category_raw, category_normalized=payload.category_normalized,
        teaser_category_id=payload.teaser_category_id,
        teaser_city_id=payload.teaser_city_id, teaser_location_id=payload.teaser_location_id,
        requested_count=payload.count, created_by_id=user.id,
    )
    db.add(batch)
    db.flush()

    candidates = select_candidates(db, payload.site_id, payload.region_raw,
                                   payload.category_raw, payload.count)
    for candidate in candidates:
        _add_company_from_candidate(db, batch, candidate)
    db.commit()
    db.refresh(batch)
    return _to_out(db, batch)


@router.get("/company-batches", response_model=list[BatchOut])
def list_batches(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    batches = db.scalars(select(CompanyBatch).order_by(CompanyBatch.id.desc())).all()
    return [_to_out(db, b) for b in batches]


@router.get("/company-batches/{batch_id}", response_model=BatchOut)
def read_batch(batch_id: int, db: Session = Depends(get_db),
              _user: User = Depends(get_current_user)):
    return _to_out(db, _get_or_404(db, batch_id))


@router.delete("/company-batches/{batch_id}/companies/{company_id}", response_model=BatchOut)
def remove_company(batch_id: int, company_id: int, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if batch.status != "selection_review":
        raise HTTPException(400, "партия уже запущена — правка списка недоступна")
    company = next((c for c in batch.companies if c.id == company_id), None)
    if company is None:
        raise HTTPException(404, "компания не найдена в этой партии")
    # Отвязываем от партии (batch_id = NULL), а не удаляем Company целиком.
    # Company-строка остаётся в таблице с прежним site_id, поэтому
    # taken_site_keys(site_id) (app/companies/selection.py) продолжает видеть
    # её site_key как «уже занятый для этого сайта» — add_next_candidate ниже
    # не предложит ту же компанию повторно сразу после вычёркивания
    # менеджером. Полное db.delete() потеряло бы это состояние: как только
    # строка исчезает из БД, taken_site_keys перестаёт её видеть, и следующий
    # /companies/next мог бы тут же добрать ровно того, кого только что
    # вычеркнули — вместо следующего по рейтингу кандидата. Симметрично
    # design-докстрингу Company.batch_id (batch.py) — SET NULL, не CASCADE,
    # потому что запись о компании независима от партии.
    #
    # Это осознанное и постоянное исключение компании для данного сайта
    # (не временное «может предложим позже») — согласуется с моделью дедупа
    # всего фича-набора: «единожды взята для сайта — взята навсегда»
    # (directions/2026-08-06-builders-import-design.md).
    #
    # Такие строки (batch_id=NULL, candidate_id НЕ NULL — пришли из реального
    # CompanyCandidate через _company_from_candidate) отличимы от будущих
    # Task 15 строк, мигрированных из старой CLI-базы: у тех candidate_id
    # будет NULL, так как они никогда не проходили через отбор кандидатов.
    company.batch_id = None
    db.commit()
    db.refresh(batch)
    return _to_out(db, batch)


@router.post("/company-batches/{batch_id}/companies/next", response_model=BatchOut)
def add_next(batch_id: int, db: Session = Depends(get_db),
            _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if batch.status != "selection_review":
        raise HTTPException(400, "партия уже запущена — правка списка недоступна")
    already = {c.site_key for c in batch.companies}
    candidate = add_next_candidate(db, batch.site_id, batch.region_raw, batch.category_raw,
                                   already_in_batch=already, excluded=set())
    if candidate is None:
        raise HTTPException(400, "больше подходящих компаний не найдено")
    _add_company_from_candidate(db, batch, candidate)
    db.commit()
    db.refresh(batch)
    return _to_out(db, batch)


@router.post("/company-batches/{batch_id}/run", response_model=BatchOut)
def run(batch_id: int, db: Session = Depends(get_db),
       _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if not batch.companies:
        raise HTTPException(400, "в партии нет компаний")
    if batch.status == "running":
        raise HTTPException(400, "партия уже выполняется")
    # Перевод в "running" синхронно, до apply_async — тот же приём, что и в
    # article_batches.run() (Task 18): защищает от двойной постановки задачи
    # в очередь при двойном клике / повторном запросе, а не только от гонки
    # внутри самой Celery-задачи.
    batch.status = "running"
    db.commit()
    run_company_batch.apply_async(args=[batch.id])
    return _to_out(db, batch)


@router.post("/companies/{company_id}/retry")
def retry(company_id: int, db: Session = Depends(get_db),
         _user: User = Depends(get_current_user)):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(404, "компания не найдена")
    if company.status in ("published", "generating"):
        detail = ("компания уже опубликована" if company.status == "published"
                  else "компания уже собирается — повторный запуск не требуется")
        raise HTTPException(400, detail)
    company.status = "generating"
    db.commit()
    retry_company.apply_async(args=[company.id])
    return {"ok": True}
