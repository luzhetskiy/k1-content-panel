"""Проекты домов: страницы раздела и запуски рерайта.
Дизайн: directions/2026-10-18-house-projects-rewrite-design.md."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db import Base


class HouseProjectPage(Base):
    """Страница проекта дома на сайте и её описание.

    status: new (ещё не переписывалась) → queued → in_work → done | failed;
    skipped — нет блока описания или страница пропала с сайта (skip_reason)."""

    __tablename__ = "house_project_pages"
    __table_args__ = (
        UniqueConstraint("site_id", "remote_id", name="uq_house_project_pages_site_remote"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    remote_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    # Содержимое блока описания до нашего первого рерайта — все повторы от него.
    # Правка описания руками в админке сайта становится новым оригиналом.
    original_html: Mapped[str] = mapped_column(Text, default="")
    rewritten_html: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    skip_reason: Mapped[str] = mapped_column(String(100), default="")
    error_text: Mapped[str] = mapped_column(Text, default="")
    rewritten_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rewrite_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectRewriteRun(Base):
    """Запуск «Запустить рерайт» по проекту. Открыт, пока finished_at пуст."""

    __tablename__ = "project_rewrite_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    job_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # new — новые и с ошибкой; all — все страницы заново.
    mode: Mapped[str] = mapped_column(String(10), default="new")
    # 0, пока идёт синхронизация: страницы ещё не отобраны.
    total: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
