from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.db import Base


class ArticleBatch(Base):
    """Партия статей: в её рамках согласуется список тем.

    Статусы: topics_pending → topics_review → running → done | failed
    """

    __tablename__ = "article_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    # SET NULL, а не CASCADE. Партия и её статьи — это журнал того, что было
    # реально опубликовано (remote_url/remote_page_id), а не производные от
    # сайта данные, которые можно потерять без сожаления. Удаление сайта
    # (delete_site, app/api/admin_sites.py, уже в проде с Task 11) не должно
    # стирать эту историю — только оборвать ссылку на уже не существующий
    # сайт. Это согласуется с уже написанным в Task 18 API: `_to_out`
    # (app/api/article_batches.py) достаёт сайт через `db.get(Site,
    # batch.site_id)` и подставляет "—", если сайта нет, — при CASCADE эта
    # ветка была бы мёртвым кодом, потому что сама партия исчезла бы вместе
    # с сайтом раньше, чем кто-то успел бы увидеть "—". Симметрично с
    # JobRun.site_id (см. app/models/job.py) — обе истории переживают
    # удаление сайта по одной и той же причине.
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    requested_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="topics_pending")
    error_text: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    articles: Mapped[list["Article"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="Article.id")


class Article(Base):
    """Одна статья.

    Статусы: draft → generating → generated → published | failed.
    `published` = черновик создан на сайте (published=false на стороне сайта);
    окончательную публикацию делает менеджер в админке сайта.
    """

    __tablename__ = "articles"
    __table_args__ = (
        # Страховка от двух черновиков с одинаковым url на одном сайте
        # (url = articles_url_prefix + slug + "/", см. app/articles/builder.py).
        # Живая проверка перед публикацией (_guard_duplicate_url, Task 16)
        # спрашивает сам сайт и защищает от дублей с уже существующими там
        # страницами, но не от гонки внутри нашей же партии, если список
        # страниц сайта кэширован или eventually-consistent и не показывает
        # только что созданную страницу. Это не замена проверке на сайте
        # (реальная уникальность url решается на его стороне), а гарантия,
        # что наша собственная БД не заведёт заведомо конфликтующую пару.
        # Частичный, а не сквозной индекс — черновики до сборки хранят
        # slug="" (default), и в одной партии их может быть много одновременно
        # (см. test_batch_articles_relationship); сквозной UniqueConstraint
        # запретил бы вторую тему в той же партии ещё до генерации текста.
        # Тот же приём частичного индекса уже применён в prompt_template.py
        # для site_id IS NULL — проверено там же: NULL/пустая строка не
        # различаются самим собой в UNIQUE, различать их надо явным WHERE.
        Index("uq_article_site_slug", "site_id", "slug", unique=True,
              postgresql_where=text("slug != ''"),
              sqlite_where=text("slug != ''")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("article_batches.id", ondelete="CASCADE"), index=True)
    # Дублирует batch.site_id — намеренная денормализация, не забытая связь.
    # retry_article_sync (Task 17, app/tasks.py) достаёт сайт статьи напрямую
    # через `db.get(Site, article.site_id)`, не заходя в её партию: для
    # повтора одной упавшей статьи знать партию не обязательно, а партия к
    # моменту повтора вообще может быть архивной. Оба поля проставляются
    # одним и тем же вызывающим кодом из одного объекта site в один момент
    # (Task 15/17: `Article(batch_id=batch.id, site_id=site.id, ...)`),
    # поэтому расхождение article.site_id != article.batch.site_id возможно
    # только при ручной правке БД в обход приложения, а не в штатном потоке.
    # SET NULL, а не CASCADE — см. комментарий у ArticleBatch.site_id выше:
    # опубликованная статья должна остаться в истории и после удаления сайта.
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    topic: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(500), default="")
    slug: Mapped[str] = mapped_column(String(200), default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    meta_description: Mapped[str] = mapped_column(Text, default="")
    meta_keywords: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    error_text: Mapped[str] = mapped_column(Text, default="")
    remote_page_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remote_url: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    batch: Mapped["ArticleBatch"] = relationship(back_populates="articles")
    images: Mapped[list["ArticleImage"]] = relationship(
        back_populates="article", cascade="all, delete-orphan", order_by="ArticleImage.position")


class ArticleImage(Base):
    __tablename__ = "article_images"
    __table_args__ = (
        # kind — фактически перечисление cover|content, но хранится строкой:
        # тип-литерал в Python (Mapped[str]) не проверяется на INSERT ни на
        # SQLite, ни на Postgres. Опечатка в новом коде (Task 16/17 пишут
        # buider.py как `kind="content"` литералом в нескольких местах) не
        # всплыла бы сразу: `_upload_content_images` находит картинку через
        # `next(i for i in article.images if i.kind == "content" ...)` —
        # непойманная опечатка при записи привела бы к необработанному
        # StopIteration на чтении, на несколько шагов дальше от места
        # ошибки. CHECK ловит опечатку в момент INSERT, а не через 2 шага
        # конвейера. Проверено эмпирически на Postgres (docker compose up -d
        # postgres, миграция применена): INSERT с kind='banner' падает с
        # `CheckViolation`, а не проходит молча.
        CheckConstraint("kind IN ('cover', 'content')", name="ck_article_image_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))       # cover | content
    position: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str] = mapped_column(Text, default="")
    remote_path: Mapped[str] = mapped_column(String(500), default="")
    cost: Mapped[float] = mapped_column(default=0.0)

    article: Mapped["Article"] = relationship(back_populates="images")
