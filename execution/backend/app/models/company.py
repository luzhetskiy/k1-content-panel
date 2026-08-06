"""Модели раздела «Строители»: импорт xlsx, пул кандидатов, партии, компании.

Портирует схему execution/db.py в Postgres — см.
directions/2026-08-06-builders-import-design.md.
"""

from datetime import datetime

from sqlalchemy import (
    DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.clock import utcnow
from app.db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


class CompanyImport(Base):
    """Журнал загрузки xlsx. Партии не ссылаются на конкретный импорт — они
    всегда работают с текущим состоянием CompanyCandidate (upsert-пул)."""

    __tablename__ = "company_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300), default="")
    uploaded_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="parsed")  # parsed|failed
    error_message: Mapped[str] = mapped_column(Text, default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanyCandidate(Base):
    """Общий пул кандидатов — один на всё приложение, не на сайт и не на
    импорт. Обновляется upsert'ом по site_key при каждой новой загрузке xlsx
    (см. app/companies/imports.py)."""

    __tablename__ = "company_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_key: Mapped[str] = mapped_column(String(300), unique=True)
    website_raw: Mapped[str] = mapped_column(String(500), default="")
    name: Mapped[str] = mapped_column(String(300), default="")
    region_raw: Mapped[str] = mapped_column(String(200), default="")
    category_raw: Mapped[str] = mapped_column(String(300), default="")
    city: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(String(500), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviews_count: Mapped[int] = mapped_column(Integer, default=0)
    ratings_count: Mapped[int] = mapped_column(Integer, default=0)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    yandex_url: Mapped[str] = mapped_column(String(500), default="")
    raw_row_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)
