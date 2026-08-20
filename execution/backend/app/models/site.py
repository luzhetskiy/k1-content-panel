from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db import Base


class Site(Base):
    """Карточка целевого сайта: доступы, разделы, стили и профиль контента.

    Заменяет собой знание, которое раньше жило в .env и в памяти агента.
    """

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(200), unique=True)

    @validates("domain")
    def _normalize_domain(self, _key: str, value: str) -> str:
        # DNS регистр не различает, а колонка — различает: без нормализации
        # example.ru и Example.ru завелись бы как два разных сайта с разными
        # токенами и эталонами, указывающие на один физический домен.
        # Нормализация в модели, а не в вызывающих: точек записи будет
        # несколько (Task 11 создаёт сайт, Task 24 правит), и любая из них
        # иначе может пройти мимо.
        return (value or "").strip().lower()

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
    # Реальные пропорции W:H картинок эталона по позициям, через запятую
    # ("1180:488,1180:631,631:631") — измеряются синхронизацией
    # (measure_reference_image_ratios, app/sites/reference.py) и берутся
    # ArticleBuilder._crop_for_position вместо единого CONTENT_CROP="3:2"
    # для всех позиций (app/articles/builder.py). Пустая строка на позиции —
    # картинку эталона не удалось скачать/измерить, генератор в этом случае
    # использует дефолтный CONTENT_CROP только для неё, а не роняет всё.
    reference_image_ratios: Mapped[str] = mapped_column(Text, default="")

    image_style_prompt: Mapped[str] = mapped_column(Text, default="")
    cover_mode: Mapped[str] = mapped_column(String(20), default="prompt")  # prompt|like_existing
    cover_style_prompt: Mapped[str] = mapped_column(Text, default="")
    watermark_path: Mapped[str] = mapped_column(String(400), default="")

    # --- строители (план 2) ---
    builder_template_html: Mapped[str] = mapped_column(Text, default="")
    builder_parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Эталонная карточка строителя на самом сайте — источник builder_template_html,
    # см. app/companies/reference.py::sync_builder_reference. Тот же приём, что
    # reference_article_id/reference_synced_at у статей.
    builder_reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    builder_reference_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
