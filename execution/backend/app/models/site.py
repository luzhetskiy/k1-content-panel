from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Site(Base):
    """Карточка целевого сайта: доступы, разделы, стили и профиль контента.

    Заменяет собой знание, которое раньше жило в .env и в памяти агента.
    """

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(200), unique=True)
    base_url: Mapped[str] = mapped_column(String(300))
    api_token_enc: Mapped[str] = mapped_column(Text)          # Fernet
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- профиль контента: основа для подбора тем и тона текста ---
    # По домену и списку прошлых заголовков модель промахивается с тематикой:
    # у стройбазы и у производителя смесей рубрика называется одинаково, а темы
    # нужны разные. Поэтому тематика задаётся явно.
    site_description: Mapped[str] = mapped_column(Text, default="")
    tone_of_voice: Mapped[str] = mapped_column(Text, default="")

    # --- статьи ---
    publish_target: Mapped[str] = mapped_column(String(20), default="pages")  # pages|articles
    # Раздел задаётся родительской страницей; её url подтягивается синхронизацией.
    # Никакого «/blog/» по умолчанию: раздел у каждого сайта свой.
    articles_parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    articles_url_prefix: Mapped[str] = mapped_column(String(200), default="")

    # Эталонная опубликованная статья — единственный источник разметки; отдельного
    # HTML-шаблона нет. Кешируется, чтобы не ходить на сайт за ней при каждой статье.
    reference_article_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_html: Mapped[str] = mapped_column(Text, default="")
    reference_images: Mapped[int] = mapped_column(Integer, default=0)
    reference_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    image_style_prompt: Mapped[str] = mapped_column(Text, default="")
    cover_mode: Mapped[str] = mapped_column(String(20), default="prompt")  # prompt|like_existing
    cover_style_prompt: Mapped[str] = mapped_column(Text, default="")
    watermark_path: Mapped[str] = mapped_column(String(400), default="")

    # --- строители (план 2) ---
    builder_template_html: Mapped[str] = mapped_column(Text, default="")
    builder_parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    teaser_category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    teaser_city_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    teaser_location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
