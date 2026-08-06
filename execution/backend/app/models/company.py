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


class CompanyBatch(Base):
    """Партия строителей. Статусы: selection_review → running → done | failed."""

    __tablename__ = "company_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    region_raw: Mapped[str] = mapped_column(String(200), default="")
    category_raw: Mapped[str] = mapped_column(String(300), default="")
    # Вводятся оператором заново при каждой партии — постоянного маппинга
    # (site, category_raw) → (category_normalized, teaser ids) сервис не
    # хранит: проще и прозрачнее скрытого автоподставления, см. design doc §4.
    category_normalized: Mapped[str] = mapped_column(String(300), default="")
    teaser_category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    teaser_city_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    teaser_location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="selection_review")
    error_text: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    companies: Mapped[list["Company"]] = relationship(
        back_populates="batch", order_by="Company.id")


class Company(Base):
    """Компания конкретного сайта. Уникальность (site_id, site_key) — это и
    есть дедуп «по каждому сайту отдельно» (design doc §1).

    Статусы: draft → generating → generated → published | failed.
    """

    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("site_id", "site_key", name="uq_company_site_site_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    # SET NULL, не CASCADE: у мигрированных из старого CLI компаний партии
    # нет вовсе (batch_id=NULL) — они не производные от партии данные, а
    # такой же самостоятельный журнал публикации, как сама Company.
    batch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("company_batches.id", ondelete="SET NULL"), nullable=True)
    candidate_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("company_candidates.id", ondelete="SET NULL"), nullable=True)
    site_key: Mapped[str] = mapped_column(String(300))
    website: Mapped[str] = mapped_column(String(500), default="")
    name: Mapped[str] = mapped_column(String(300), default="")
    region: Mapped[str] = mapped_column(String(200), default="")
    category_normalized: Mapped[str] = mapped_column(String(300), default="")
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviews_count: Mapped[int] = mapped_column(Integer, default=0)
    yandex_url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    error_text: Mapped[str] = mapped_column(Text, default="")
    remote_page_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remote_url: Mapped[str] = mapped_column(String(500), default="")
    teaser_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    batch: Mapped["CompanyBatch | None"] = relationship(back_populates="companies")
    info: Mapped["CompanyInfo | None"] = relationship(
        back_populates="company", uselist=False, cascade="all, delete-orphan")


class CompanyInfo(Base):
    """Данные для шаблона builder_template_html. Поля из YANDEX_FIELDS
    приходят из выгрузки Яндекс.Карт и достоверны — RouterAI (Task 13) их не
    трогает, переписывает только about_company/specialization/
    projects_services/benefits."""

    __tablename__ = "company_info"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), unique=True)
    builder_name: Mapped[str] = mapped_column(String(300), default="")
    city_name: Mapped[str] = mapped_column(String(200), default="")
    city_prepositional: Mapped[str] = mapped_column(String(200), default="")
    builder_logo_src: Mapped[str] = mapped_column(String(500), default="")
    builder_logo_alt: Mapped[str] = mapped_column(String(300), default="")
    about_company: Mapped[str] = mapped_column(Text, default="")
    specialization: Mapped[str] = mapped_column(Text, default="")
    projects_services: Mapped[str] = mapped_column(Text, default="")
    benefits: Mapped[str] = mapped_column(Text, default="")
    contacts: Mapped[list] = mapped_column(JsonType, default=list)
    address: Mapped[str] = mapped_column(String(500), default="")
    coordinates: Mapped[str] = mapped_column(String(100), default="")
    scraped_text: Mapped[str] = mapped_column(Text, default="")
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    company: Mapped["Company"] = relationship(back_populates="info")


# Поля CompanyInfo, достоверные из выгрузки Яндекс.Карт — RouterAI (app/companies/builder.py,
# Task 13) не имеет права их переписывать, только about_company/specialization/
# projects_services/benefits.
YANDEX_INFO_FIELDS = (
    "builder_name", "city_name", "city_prepositional",
    "builder_logo_src", "builder_logo_alt", "contacts", "address", "coordinates",
)
