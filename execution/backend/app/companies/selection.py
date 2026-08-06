"""Отбор кандидатов в партию: фильтр региона+категории, исключение уже
взятых для сайта, сортировка по отзывам. См. design doc §4."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company, CompanyCandidate


def _taken_site_keys(db: Session, site_id: int) -> set[str]:
    return {c.site_key for c in
           db.scalars(select(Company).where(Company.site_id == site_id)).all()}


def select_candidates(db: Session, site_id: int, region_raw: str, category_raw: str,
                      count: int) -> list[CompanyCandidate]:
    taken = _taken_site_keys(db, site_id)
    matching = db.scalars(
        select(CompanyCandidate).where(
            CompanyCandidate.region_raw == region_raw,
            CompanyCandidate.category_raw == category_raw,
        )
    ).all()
    available = [c for c in matching if c.site_key not in taken]
    available.sort(key=lambda c: -c.reviews_count)
    return available[:count]


def add_next_candidate(db: Session, site_id: int, region_raw: str, category_raw: str,
                       already_in_batch: set[str], excluded: set[str]) -> CompanyCandidate | None:
    """Следующий по рейтингу кандидат, не входящий ни в партию, ни в список
    вычеркнутых менеджером, ни уже взятый для сайта где-либо ещё."""
    taken = _taken_site_keys(db, site_id)
    matching = db.scalars(
        select(CompanyCandidate).where(
            CompanyCandidate.region_raw == region_raw,
            CompanyCandidate.category_raw == category_raw,
        )
    ).all()
    skip = taken | already_in_batch | excluded
    available = [c for c in matching if c.site_key not in skip]
    if not available:
        return None
    return max(available, key=lambda c: c.reviews_count)
