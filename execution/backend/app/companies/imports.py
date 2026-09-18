"""Импорт xlsx в общий пул кандидатов (upsert по site_key) и выборка facets
для форм создания партии. См. directions/2026-08-06-builders-import-design.md §3."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.companies.import_xlsx import COLUMNS, XlsxParseError, parse_workbook
from app.companies.selection import taken_site_keys
from app.models.company import CompanyCandidate, CompanyImport

logger = logging.getLogger(__name__)


# Поля ParsedRow → колонка файла, как её видит пользователь в сообщении об ошибке.
_FIELD_LABELS = {
    "site_key": COLUMNS["site"],
    "website_raw": COLUMNS["site"],
    "name": COLUMNS["name"],
    "region_raw": COLUMNS["region"],
    "category_raw": COLUMNS["category"],
    "city": COLUMNS["city"],
    "address": COLUMNS["address"],
    "phone": "Телефон",
    "email": COLUMNS["email"],
    "yandex_url": COLUMNS["yandex_card"],
}

_MAX_REPORTED = 3


def _too_long_values(rows) -> list[tuple[str, str, int, int]]:
    """(компания, колонка, длина, лимит) для значений длиннее String(n) колонки.
    Проверяем заранее: Postgres откатывает весь файл без указания строки, а
    SQLite в тестах длину не проверяет вовсе."""
    limits = {
        name: col.type.length
        for name, col in CompanyCandidate.__table__.columns.items()
        if name in _FIELD_LABELS and getattr(col.type, "length", None)
    }
    found = []
    for row in rows:
        for field_name, limit in limits.items():
            length = len(getattr(row, field_name) or "")
            if length > limit:
                found.append((row.name, _FIELD_LABELS[field_name], length, limit))
    return found


def _too_long_message(found: list[tuple[str, str, int, int]]) -> str:
    parts = [f"«{name}» — «{label}»: {length} симв. при лимите {limit}"
             for name, label, length, limit in found[:_MAX_REPORTED]]
    message = "слишком длинные значения, файл не загружен: " + "; ".join(parts)
    if len(found) > _MAX_REPORTED:
        message += f" и ещё {len(found) - _MAX_REPORTED}"
    return message


def import_file(db: Session, data: bytes, filename: str,
                uploaded_by_id: int | None) -> CompanyImport:
    try:
        rows = parse_workbook(data)
    except XlsxParseError as exc:
        imp = CompanyImport(filename=filename, uploaded_by_id=uploaded_by_id,
                            status="failed", error_message=str(exc))
        db.add(imp)
        db.commit()
        return imp
    except Exception:
        # Файл не распознан даже как валидный xlsx (битый zip и т.п.) —
        # openpyxl падает раньше, чем успевает сработать XlsxParseError.
        imp = CompanyImport(filename=filename, uploaded_by_id=uploaded_by_id,
                            status="failed",
                            error_message="не удалось прочитать файл — проверьте, что это корректный xlsx")
        db.add(imp)
        db.commit()
        return imp

    too_long = _too_long_values(rows)
    if too_long:
        imp = CompanyImport(filename=filename, uploaded_by_id=uploaded_by_id,
                            row_count=len(rows), status="failed",
                            error_message=_too_long_message(too_long))
        db.add(imp)
        db.commit()
        return imp

    imp = CompanyImport(filename=filename, uploaded_by_id=uploaded_by_id,
                        row_count=len(rows), matched_count=len(rows), status="parsed")
    db.add(imp)
    db.flush()

    existing_by_key = {
        c.site_key: c for c in
        db.scalars(select(CompanyCandidate).where(
            CompanyCandidate.site_key.in_({row.site_key for row in rows})
        )).all()
    }

    for row in rows:
        existing = existing_by_key.get(row.site_key)
        if existing is None:
            existing = CompanyCandidate(site_key=row.site_key)
            db.add(existing)
            existing_by_key[row.site_key] = existing
        existing.website_raw = row.website_raw
        existing.name = row.name
        existing.region_raw = row.region_raw
        existing.category_raw = row.category_raw
        existing.city = row.city
        existing.address = row.address
        existing.phone = row.phone
        existing.email = row.email
        existing.working_hours = row.working_hours
        existing.logo_url = row.logo_url
        existing.rating = row.rating
        existing.reviews_count = row.reviews_count
        existing.ratings_count = row.ratings_count
        existing.lat = row.lat
        existing.lon = row.lon
        existing.yandex_url = row.yandex_url
        existing.raw_row_json = row.raw_row
        existing.updated_at = utcnow()

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("import_file: не удалось сохранить импорт %r (%d кандидатов)",
                         filename, len(existing_by_key))
        imp.status = "failed"
        imp.error_message = "не удалось сохранить компании — проверьте данные файла"
        db.add(imp)
        db.commit()
        return imp

    return imp


@dataclass
class Facets:
    regions: list[str]
    categories: list[str]


def get_facets(db: Session, site_id: int) -> Facets:
    """Различные region_raw/category_raw в пуле, у которых для этого сайта
    есть хотя бы один ещё не взятый кандидат."""
    taken_keys = taken_site_keys(db, site_id)
    candidates = db.scalars(select(CompanyCandidate)).all()
    available = [c for c in candidates if c.site_key not in taken_keys]
    regions = sorted({c.region_raw for c in available if c.region_raw})
    categories = sorted({c.category_raw for c in available if c.category_raw})
    return Facets(regions=regions, categories=categories)
