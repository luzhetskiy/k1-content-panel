from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PromptTemplate(Base):
    """Шаблон промпта. site_id IS NULL — глобальный дефолт, иначе переопределение
    для конкретного сайта."""

    __tablename__ = "prompt_templates"
    __table_args__ = (UniqueConstraint("key", "site_id", name="uq_prompt_key_site"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50))
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="CASCADE"), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
