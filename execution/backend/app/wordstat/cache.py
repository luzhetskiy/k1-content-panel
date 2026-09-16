"""Кеш ответов Wordstat. Статистика за 30 дней меняется медленно, а квота —
100 запросов в час, поэтому перегенерация категории берёт данные отсюда."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc, utcnow
from app.models.category_meta import WordstatCache

CACHE_TTL = timedelta(days=30)


def _row(db: Session, kind: str, phrase: str, region_id: int | None) -> WordstatCache | None:
    return db.scalars(select(WordstatCache).where(
        WordstatCache.kind == kind, WordstatCache.phrase == phrase,
        WordstatCache.region_id == (region_id or 0))).first()


def cache_get(db: Session, kind: str, phrase: str, region_id: int | None,
              now: datetime | None = None) -> dict | None:
    row = _row(db, kind, phrase, region_id)
    if row is None or as_utc(row.fetched_at) < (now or utcnow()) - CACHE_TTL:
        return None
    return row.body


def cache_put(db: Session, kind: str, phrase: str, region_id: int | None, body: dict,
              now: datetime | None = None) -> None:
    row = _row(db, kind, phrase, region_id)
    if row is None:
        row = WordstatCache(kind=kind, phrase=phrase, region_id=region_id or 0)
        db.add(row)
    row.body = body
    row.fetched_at = now or utcnow()
    db.commit()
