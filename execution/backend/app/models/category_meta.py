"""Метатеги категорий каталога: категории сайта, запуски обновления, кеш и
журнал обращений к Wordstat. Дизайн: directions/2026-09-16-category-meta-design.md."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db import Base
from app.models.job import JsonType


class CategoryMeta(Base):
    """Одна категория каталога сайта и её метатеги.

    status: new (ещё не обрабатывалась) → queued → in_work → done | failed;
    skipped — категория отсеяна синхронизацией (skip_reason)."""

    __tablename__ = "category_meta"
    __table_args__ = (
        UniqueConstraint("site_id", "remote_id", name="uq_category_meta_site_remote"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: категории — производное от сайта, без него они не имеют смысла
    # (в отличие от JobRun, который переживает удаление сайта как журнал расходов).
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    remote_id: Mapped[int] = mapped_column(Integer)
    remote_parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    path: Mapped[str] = mapped_column(String(1000), default="")
    slug: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(255), default="")
    skip_reason: Mapped[str] = mapped_column(String(100), default="")

    # Фраза перезапрашивается у LLM только при переименовании категории —
    # seed_source_name хранит имя, по которому она получена.
    seed_source_name: Mapped[str] = mapped_column(String(300), default="")
    seed_phrase: Mapped[str] = mapped_column(String(300), default="")
    form_nominative: Mapped[str] = mapped_column(String(300), default="")
    form_buy: Mapped[str] = mapped_column(String(300), default="")
    form_price: Mapped[str] = mapped_column(String(300), default="")

    chosen_form: Mapped[str] = mapped_column(String(20), default="")  # nominative|declined
    nominative_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    declined_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    low_demand: Mapped[bool] = mapped_column(Boolean, default=False)

    title: Mapped[str] = mapped_column(String(255), default="")
    h1: Mapped[str] = mapped_column(String(255), default="")
    meta_description: Mapped[str] = mapped_column(Text, default="")
    meta_keywords: Mapped[str] = mapped_column(Text, default="")
    ai_keywords: Mapped[str] = mapped_column(Text, default="")
    # Что стояло в метатеге сайта до ПЕРВОЙ нашей записи; {} — метатега не было.
    previous_json: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    remote_metatag_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="new")
    error_text: Mapped[str] = mapped_column(Text, default="")
    # Когда освободится квота Wordstat — для подписи «продолжим ~в 15:05».
    wait_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MetaRun(Base):
    """Запуск «Обновить метатеги» по проекту. Открыт, пока finished_at пуст."""

    __tablename__ = "meta_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    # Расход LLM всего запуска — в одну строку журнала, а не по строке на категорию.
    job_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WordstatCache(Base):
    """Ответ Wordstat как есть. kind: top | exact | regions. TTL — в app/wordstat/cache.py."""

    __tablename__ = "wordstat_cache"
    __table_args__ = (
        UniqueConstraint("kind", "phrase", "region_id", name="uq_wordstat_cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    phrase: Mapped[str] = mapped_column(String(400), default="")
    region_id: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[dict] = mapped_column(JsonType, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WordstatCall(Base):
    """Одна строка — один запрос к Wordstat. По ним считается квота за час."""

    __tablename__ = "wordstat_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
