from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text

from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PromptTemplate(Base):
    """Шаблон промпта. site_id IS NULL — глобальный дефолт, иначе переопределение
    для конкретного сайта."""

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("key", "site_id", name="uq_prompt_key_site"),
        # Отдельный частичный индекс на глобальные шаблоны: UniqueConstraint выше
        # их НЕ различает, потому что в SQL NULL не равен сам себе — проверено,
        # две строки с одним key и site_id=NULL вставляются в Postgres успешно.
        # Разрешение промпта берёт первую попавшуюся, то есть какой из дублей
        # уедет в модель, зависело бы от порядка строк.
        Index("uq_prompt_key_global", "key", unique=True,
              postgresql_where=text("site_id IS NULL"),
              sqlite_where=text("site_id IS NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50))
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="CASCADE"), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
