"""Глобальный ограничитель запросов к Wordstat — один на все проекты.

Квота Яндекса — 100 запросов в час. Считаем по журналу wordstat_calls за
скользящий час. Резервирование — под advisory-блокировкой Postgres: два
воркера одновременно не превысят лимит. В SQLite (тесты) блокировки нет — там
один поток.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.clock import as_utc, utcnow
from app.models.category_meta import WordstatCall

WINDOW = timedelta(hours=1)
LOCK_KEY = 20260916


class QuotaExceeded(RuntimeError):
    """Квоты на этот час нет; seconds — через сколько освободится нужное число слотов."""

    def __init__(self, seconds: float):
        super().__init__(f"квота Wordstat исчерпана, освободится через {int(seconds)} с")
        self.seconds = seconds


def _lock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})


def reserve(db: Session, count: int, limit: int, now: datetime | None = None) -> float:
    """0.0 — слоты зарезервированы (строки журнала записаны и закоммичены);
    больше нуля — столько секунд ждать, ничего не записано."""
    if count <= 0:
        return 0.0
    if count > limit:
        raise ValueError(f"нельзя зарезервировать {count} запросов при лимите {limit} в час")
    now = now or utcnow()
    _lock(db)
    db.execute(delete(WordstatCall).where(WordstatCall.called_at < now - 2 * WINDOW))
    calls = [as_utc(moment) for moment in db.scalars(
        select(WordstatCall.called_at)
        .where(WordstatCall.called_at > now - WINDOW)
        .order_by(WordstatCall.called_at)).all()]
    overflow = len(calls) + count - limit
    if overflow <= 0:
        db.add_all([WordstatCall(called_at=now) for _ in range(count)])
        db.commit()
        return 0.0
    db.commit()   # фиксирует чистку и отпускает блокировку
    frees_at = calls[overflow - 1] + WINDOW
    return max(1.0, (frees_at - now).total_seconds())
