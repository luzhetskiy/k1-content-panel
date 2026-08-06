# Раздел «Строители»: импорт и партии — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перенести раздел «Строители» из CLI в веб-сервис: загрузка xlsx-выгрузки Яндекс.Карт, отбор компаний в партию по региону/категории с превью и добором, автоматическая генерация текста через RouterAI, публикация черновика страницы и карточки-тизера, перенос уже обработанных CLI-компаний.

**Architecture:** Backend — FastAPI + SQLAlchemy + Celery, по образцу раздела «Статьи» (`app/tasks.py`, `app/articles/*`, `app/api/article_batches.py`): `*_sync(db, ...)` функции, обёрнутые тонкими Celery-задачами, JobRun/LlmUsage для журнала. Frontend — React + antd, по образцу `ArticlesPage.tsx`/`BatchPage.tsx`. Источник истины по спецификации — `directions/2026-08-06-builders-import-design.md`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Celery, PostgreSQL 16 (SQLite в тестах), openpyxl, BeautifulSoup4, requests; React 18 + TypeScript + antd 5.

---

## Контекст для исполнителя

Проект уже содержит рабочий раздел «Статьи» — читай его код как образец паттернов
(`execution/backend/app/models/article.py`, `app/api/article_batches.py`,
`app/tasks.py`, `app/articles/builder.py`, `app/sites/client.py`,
`app/ai/prompts.py`). Раздел «Строители» повторяет ту же архитектуру:
партия → превью/согласование → запуск → фоновая сборка по одной сущности.

Старые CLI-скрипты (`execution/step1_import_yandex.py`, `step2_scrape_company.py`,
`step3_fill_template.py`, `step6_manage_teasers.py`, `execution/db.py`) не
удаляются и не трогаются — их логика **портируется** (переписывается на новом
месте), а не импортируется напрямую.

Все backend-файлы — внутри `execution/backend/`. Все команды ниже выполняются
из этой директории, если не сказано иное.

**Отклонение от буквы дизайн-документа.** Design doc §2 описывает
`generated_content` как отдельную таблицу, перенесённую из `execution/db.py`
как есть. В этом плане она не заводится: в старой схеме `generated_content`
была нужна как связка many-to-many (одна `companies`-строка — глобальная по
сайту компании — могла иметь черновики на нескольких целевых сайтах). В новой
схеме `Company` уже привязана к ровно одному `site_id`, поэтому поля
`generated_content` (`page_url`→`remote_url`, `published`→`status`) легли
прямо на `Company` (Task 2) — отдельная таблица только дублировала бы то, что
уже есть на самой компании. Функционально ничего не потеряно.

---

## Task 1: Модели — импорт и пул кандидатов

**Files:**
- Create: `execution/backend/app/models/company.py`
- Modify: `execution/backend/app/models/__init__.py`
- Test: `execution/backend/tests/test_models_company.py`

- [ ] **Step 1: Написать падающий тест на CompanyImport и CompanyCandidate**

```python
# execution/backend/tests/test_models_company.py
from app.models.company import CompanyCandidate, CompanyImport


def test_company_import_defaults(db_session):
    imp = CompanyImport(filename="builders.xlsx", row_count=100)
    db_session.add(imp)
    db_session.commit()
    assert imp.status == "parsed"
    assert imp.matched_count == 0
    assert imp.error_count == 0


def test_company_candidate_site_key_is_unique(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add(CompanyCandidate(site_key="stroyka.ru", name="ООО Стройка"))
    db_session.commit()
    db_session.add(CompanyCandidate(site_key="stroyka.ru", name="Дубль"))
    try:
        db_session.commit()
        assert False, "ожидался IntegrityError на повторном site_key"
    except IntegrityError:
        db_session.rollback()
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `cd execution/backend && pytest tests/test_models_company.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'app.models.company'`

- [ ] **Step 3: Создать модели**

```python
# execution/backend/app/models/company.py
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
```

- [ ] **Step 4: Зарегистрировать модели в `app/models/__init__.py`**

```python
# execution/backend/app/models/__init__.py
from app.models.article import Article, ArticleBatch, ArticleImage
from app.models.company import CompanyCandidate, CompanyImport
from app.models.job import JobRun, LlmUsage
from app.models.prompt_template import PromptTemplate
from app.models.setting import Setting
from app.models.site import Site
from app.models.user import User

__all__ = [
    "Article", "ArticleBatch", "ArticleImage",
    "CompanyCandidate", "CompanyImport",
    "JobRun", "LlmUsage",
    "PromptTemplate", "Setting", "Site", "User",
]
```

- [ ] **Step 5: Запустить тест и убедиться, что он проходит**

Run: `pytest tests/test_models_company.py -v`
Expected: PASS (2 теста)

- [ ] **Step 6: Commit**

```bash
git add app/models/company.py app/models/__init__.py tests/test_models_company.py
git commit -m "feat: модели CompanyImport и CompanyCandidate"
```

---

## Task 2: Модели — партия, компания, company_info

**Files:**
- Modify: `execution/backend/app/models/company.py`
- Modify: `execution/backend/tests/test_models_company.py`

- [ ] **Step 1: Дописать падающие тесты**

```python
# добавить в execution/backend/tests/test_models_company.py
from app.models.company import Company, CompanyBatch, CompanyInfo


def test_company_batch_starts_in_selection_review(db_session):
    batch = CompanyBatch(site_id=1, region_raw="Самара", category_raw="Строительство домов",
                         category_normalized="Дома под ключ", requested_count=10)
    db_session.add(batch)
    db_session.commit()
    assert batch.status == "selection_review"


def test_company_unique_per_site_and_site_key(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add(Company(site_id=1, site_key="stroyka.ru", name="А"))
    db_session.commit()
    db_session.add(Company(site_id=1, site_key="stroyka.ru", name="Б"))
    try:
        db_session.commit()
        assert False, "ожидался IntegrityError на дубле (site_id, site_key)"
    except IntegrityError:
        db_session.rollback()


def test_same_site_key_allowed_on_different_sites(db_session):
    db_session.add_all([
        Company(site_id=1, site_key="stroyka.ru", name="А"),
        Company(site_id=2, site_key="stroyka.ru", name="А"),
    ])
    db_session.commit()   # не должно бросить IntegrityError


def test_company_info_one_to_one(db_session):
    company = Company(site_id=1, site_key="stroyka.ru", name="А")
    db_session.add(company)
    db_session.commit()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Стройка"))
    db_session.commit()
    db_session.refresh(company)
    assert company.info.builder_name == "ООО Стройка"
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_models_company.py -v`
Expected: FAIL — `ImportError: cannot import name 'Company'`

- [ ] **Step 3: Дописать модели в `app/models/company.py`**

```python
# добавить в конец execution/backend/app/models/company.py
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
```

- [ ] **Step 4: Дописать регистрацию в `app/models/__init__.py`**

```python
# execution/backend/app/models/__init__.py — заменить строку импорта company на:
from app.models.company import (
    Company, CompanyBatch, CompanyCandidate, CompanyImport, CompanyInfo,
)
```

И добавить `"Company", "CompanyBatch", "CompanyInfo",` в `__all__` рядом с
`"CompanyCandidate", "CompanyImport",`.

- [ ] **Step 5: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_models_company.py -v`
Expected: PASS (6 тестов)

- [ ] **Step 6: Commit**

```bash
git add app/models/company.py app/models/__init__.py tests/test_models_company.py
git commit -m "feat: модели CompanyBatch, Company, CompanyInfo"
```

---

## Task 3: Alembic-миграция

**Files:**
- Create: `execution/backend/alembic/versions/a1c8f0d93b7e_companies.py`

Текущий head-миграции — `450fdec97dd5` (см. `alembic/versions/450fdec97dd5_created_by_id_set_null_on_delete.py`). Новая миграция ставится поверх него. Пишется вручную по образцу `e25842d72da3_articles_and_jobs.py`, а не автогенерацией — БД для `alembic revision --autogenerate` в этой сессии не поднята.

- [ ] **Step 1: Создать файл миграции**

```python
# execution/backend/alembic/versions/a1c8f0d93b7e_companies.py
"""companies

Revision ID: a1c8f0d93b7e
Revises: 450fdec97dd5
Create Date: 2026-08-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a1c8f0d93b7e'
down_revision: Union[str, None] = '450fdec97dd5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('company_imports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=300), nullable=False),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('matched_count', sa.Integer(), nullable=False),
        sa.Column('error_count', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('company_candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_key', sa.String(length=300), nullable=False),
        sa.Column('website_raw', sa.String(length=500), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('region_raw', sa.String(length=200), nullable=False),
        sa.Column('category_raw', sa.String(length=300), nullable=False),
        sa.Column('city', sa.String(length=200), nullable=False),
        sa.Column('address', sa.String(length=500), nullable=False),
        sa.Column('phone', sa.String(length=50), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=False),
        sa.Column('rating', sa.Float(), nullable=True),
        sa.Column('reviews_count', sa.Integer(), nullable=False),
        sa.Column('ratings_count', sa.Integer(), nullable=False),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lon', sa.Float(), nullable=True),
        sa.Column('yandex_url', sa.String(length=500), nullable=False),
        sa.Column('raw_row_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_key'),
    )
    op.create_table('company_batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('region_raw', sa.String(length=200), nullable=False),
        sa.Column('category_raw', sa.String(length=300), nullable=False),
        sa.Column('category_normalized', sa.String(length=300), nullable=False),
        sa.Column('teaser_category_id', sa.Integer(), nullable=True),
        sa.Column('teaser_city_id', sa.Integer(), nullable=True),
        sa.Column('teaser_location_id', sa.Integer(), nullable=True),
        sa.Column('requested_count', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error_text', sa.Text(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('companies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('batch_id', sa.Integer(), nullable=True),
        sa.Column('candidate_id', sa.Integer(), nullable=True),
        sa.Column('site_key', sa.String(length=300), nullable=False),
        sa.Column('website', sa.String(length=500), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('region', sa.String(length=200), nullable=False),
        sa.Column('category_normalized', sa.String(length=300), nullable=False),
        sa.Column('rating', sa.Float(), nullable=True),
        sa.Column('reviews_count', sa.Integer(), nullable=False),
        sa.Column('yandex_url', sa.String(length=500), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error_text', sa.Text(), nullable=False),
        sa.Column('remote_page_id', sa.Integer(), nullable=True),
        sa.Column('remote_url', sa.String(length=500), nullable=False),
        sa.Column('teaser_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['company_batches.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['candidate_id'], ['company_candidates.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_id', 'site_key', name='uq_company_site_site_key'),
    )
    op.create_table('company_info',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('builder_name', sa.String(length=300), nullable=False),
        sa.Column('city_name', sa.String(length=200), nullable=False),
        sa.Column('city_prepositional', sa.String(length=200), nullable=False),
        sa.Column('builder_logo_src', sa.String(length=500), nullable=False),
        sa.Column('builder_logo_alt', sa.String(length=300), nullable=False),
        sa.Column('about_company', sa.Text(), nullable=False),
        sa.Column('specialization', sa.Text(), nullable=False),
        sa.Column('projects_services', sa.Text(), nullable=False),
        sa.Column('benefits', sa.Text(), nullable=False),
        sa.Column('contacts', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('address', sa.String(length=500), nullable=False),
        sa.Column('coordinates', sa.String(length=100), nullable=False),
        sa.Column('scraped_text', sa.Text(), nullable=False),
        sa.Column('scraped_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id'),
    )


def downgrade() -> None:
    op.drop_table('company_info')
    op.drop_table('companies')
    op.drop_table('company_batches')
    op.drop_table('company_candidates')
    op.drop_table('company_imports')
```

- [ ] **Step 2: Проверить, что миграция применяется на пустой Postgres**

Run: `docker compose up -d postgres && docker compose run --rm backend alembic upgrade head`
Expected: команда завершается без ошибок, в логе видно применение `a1c8f0d93b7e`

- [ ] **Step 3: Проверить откат**

Run: `docker compose run --rm backend alembic downgrade -1 && docker compose run --rm backend alembic upgrade head`
Expected: обе команды проходят без ошибок

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/a1c8f0d93b7e_companies.py
git commit -m "feat: миграция таблиц раздела «Строители»"
```

---

## Task 4: Парсинг xlsx

**Files:**
- Create: `execution/backend/app/companies/__init__.py`
- Create: `execution/backend/app/companies/import_xlsx.py`
- Test: `execution/backend/tests/test_companies_import_xlsx.py`

Портирует `execution/step1_import_yandex.py`: `build_header_map`, `site_key`,
плюс новое правило — `category_raw` берёт только первый сегмент до `|`.

- [ ] **Step 1: Написать падающие тесты**

```python
# execution/backend/tests/test_companies_import_xlsx.py
import io

import openpyxl
import pytest

from app.companies.import_xlsx import ParsedRow, XlsxParseError, parse_workbook, site_key


def _make_workbook(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Запрос", "Название", "Категории", "Регион", "Город", "Полный адрес",
              "Мобильные", "Немобильные", "Сайт", "Email с сайта компании", "График",
              "Широта", "Долгота", "Оценок", "Отзывов", "Рейтинг"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_site_key_normalizes_url():
    assert site_key("https://www.Stroyka.ru/") == "stroyka.ru"
    assert site_key("http://stroyka.ru") == "stroyka.ru"
    assert site_key("") == ""


def test_category_raw_takes_first_segment_before_pipe():
    data = _make_workbook([
        ["застройщик", "ООО Дом", "Строительство дачных домов и коттеджей | бани | стройка",
         "Самарская область", "Самара", "ул. Ленина 1", "", "+7 846 000-00-00",
         "https://dom-samara.ru", "info@dom-samara.ru", "", "", "", 10, 5, 4.8],
    ])
    rows = parse_workbook(data)
    assert rows[0].category_raw == "Строительство дачных домов и коттеджей"


def test_row_without_site_is_dropped():
    data = _make_workbook([
        ["застройщик", "ООО Без сайта", "Категория", "Самара", "Самара", "", "", "",
         "", "", "", "", "", 0, 0, None],
    ])
    rows = parse_workbook(data)
    assert rows == []


def test_missing_required_column_raises():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Название"])   # нет "Сайт", "Регион" и т.д.
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(XlsxParseError):
        parse_workbook(buf.getvalue())


def test_duplicate_site_key_within_file_collapses_to_one_row():
    data = _make_workbook([
        ["з", "ООО Дом", "Кат", "Самара", "Самара", "", "", "", "https://dom.ru", "", "",
         "", "", 5, 3, 4.5],
        ["з", "ООО Дом (дубль)", "Кат", "Самара", "Самара", "", "", "",
         "https://www.dom.ru/", "", "", "", "", 8, 6, 4.7],
    ])
    rows = parse_workbook(data)
    assert len(rows) == 1
    assert rows[0].reviews_count == 6   # последняя встреченная строка побеждает
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_companies_import_xlsx.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.companies'`

- [ ] **Step 3: Создать пакет и модуль парсинга**

```python
# execution/backend/app/companies/__init__.py
```

```python
# execution/backend/app/companies/import_xlsx.py
"""Парсинг выгрузки Яндекс.Карт (xlsx). Портирует build_header_map/site_key
из execution/step1_import_yandex.py; category_raw дополнительно обрезается
до первого сегмента перед '|' — см. directions/2026-08-06-builders-import-design.md §2."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import openpyxl

COLUMNS = {
    "name": "Название",
    "category": "Категории",
    "region": "Регион",
    "city": "Город",
    "address": "Полный адрес",
    "phone_mobile": "Мобильные",
    "phone_landline": "Немобильные",
    "site": "Сайт",
    "email": "Email с сайта компании",
    "lat": "Широта",
    "lon": "Долгота",
    "ratings": "Оценок",
    "reviews": "Отзывов",
    "rating": "Рейтинг",
    "yandex_card": "Карточка организации",
}

REQUIRED_KEYS = ("name", "region", "city", "category", "site")


class XlsxParseError(RuntimeError):
    pass


@dataclass
class ParsedRow:
    site_key: str
    website_raw: str
    name: str
    region_raw: str
    category_raw: str
    city: str
    address: str = ""
    phone: str = ""
    email: str = ""
    rating: float | None = None
    reviews_count: int = 0
    ratings_count: int = 0
    lat: float | None = None
    lon: float | None = None
    yandex_url: str = ""
    raw_row: dict = field(default_factory=dict)


def site_key(url: str) -> str:
    """Нормализованный ключ сайта: без схемы, www, слэша, регистра."""
    if not url:
        return ""
    s = str(url).lower().strip()
    s = re.sub(r"^https?://", "", s)
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/")


def _normalize_site_url(url) -> str:
    if not url:
        return ""
    raw = str(url).strip().split("|")[0].strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def _category_first_segment(value) -> str:
    """Значение колонки «Категории» вида «А | Б | В» — берём только «А»:
    остальное — уточнения источника, вносящие путаницу в справочник категорий."""
    if not value:
        return ""
    return str(value).split("|")[0].strip()


def _to_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _to_int(val) -> int:
    try:
        return int(float(str(val).replace(",", ".")))
    except (ValueError, TypeError):
        return 0


def _build_header_map(header_row: tuple) -> dict:
    title_to_idx = {}
    for idx, title in enumerate(header_row):
        if title is not None:
            title_to_idx[str(title).strip()] = idx
    mapping = {key: title_to_idx[title] for key, title in COLUMNS.items()
              if title in title_to_idx}
    missing = [COLUMNS[k] for k in REQUIRED_KEYS if k not in mapping]
    if missing:
        raise XlsxParseError(f"В файле нет обязательных колонок: {missing}")
    return mapping


def _get(row: tuple, header: dict, key: str):
    idx = header.get(key)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def parse_workbook(data: bytes) -> list[ParsedRow]:
    """Парсит xlsx целиком. Строки без сайта отбрасываются. При дублях
    site_key внутри файла остаётся последняя встреченная строка."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows_iter = ws.iter_rows(values_only=True)
        header = _build_header_map(next(rows_iter))

        by_key: dict[str, ParsedRow] = {}
        for row in rows_iter:
            website_raw = _normalize_site_url(_get(row, header, "site"))
            if not website_raw:
                continue
            key = site_key(website_raw)
            if not key:
                continue

            name = str(_get(row, header, "name") or "").strip()
            if not name:
                continue

            phone = (_get(row, header, "phone_landline")
                     or _get(row, header, "phone_mobile") or "")

            by_key[key] = ParsedRow(
                site_key=key,
                website_raw=website_raw,
                name=name,
                region_raw=str(_get(row, header, "region") or "").strip(),
                category_raw=_category_first_segment(_get(row, header, "category")),
                city=str(_get(row, header, "city") or "").strip(),
                address=str(_get(row, header, "address") or "").strip(),
                phone=str(phone).split("|")[0].strip() if phone else "",
                email=str(_get(row, header, "email") or "").split(",")[0].strip(),
                rating=_to_float(_get(row, header, "rating")),
                reviews_count=_to_int(_get(row, header, "reviews")),
                ratings_count=_to_int(_get(row, header, "ratings")),
                lat=_to_float(_get(row, header, "lat")),
                lon=_to_float(_get(row, header, "lon")),
                yandex_url=str(_get(row, header, "yandex_card") or "").strip(),
                raw_row={str(k): (v if isinstance(row[idx], (str, int, float, type(None))) else str(v))
                        for k, idx in header.items() for v in [row[idx]]},
            )
        return list(by_key.values())
    finally:
        wb.close()
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_companies_import_xlsx.py -v`
Expected: PASS (5 тестов)

- [ ] **Step 5: Commit**

```bash
git add app/companies/__init__.py app/companies/import_xlsx.py tests/test_companies_import_xlsx.py
git commit -m "feat: парсинг выгрузки Яндекс.Карт для раздела «Строители»"
```

---

## Task 5: Импорт-сервис — upsert в пул кандидатов и facets

**Files:**
- Create: `execution/backend/app/companies/imports.py`
- Test: `execution/backend/tests/test_companies_imports.py`

- [ ] **Step 1: Написать падающие тесты**

```python
# execution/backend/tests/test_companies_imports.py
import io

import openpyxl

from app.companies.imports import get_facets, import_file
from app.models.company import Company, CompanyCandidate


def _wb_bytes(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Название", "Категории", "Регион", "Город", "Сайт", "Оценок", "Отзывов", "Рейтинг"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_creates_candidates(db_session):
    data = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 5, 3, 4.5]])
    imp = import_file(db_session, data, "builders.xlsx", uploaded_by_id=None)
    assert imp.row_count == 1
    assert imp.matched_count == 1
    candidates = db_session.query(CompanyCandidate).all()
    assert len(candidates) == 1
    assert candidates[0].site_key == "dom.ru"


def test_reimport_upserts_existing_candidate(db_session):
    first = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 5, 3, 4.5]])
    import_file(db_session, first, "builders.xlsx", uploaded_by_id=None)

    second = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 20, 15, 4.9]])
    import_file(db_session, second, "builders2.xlsx", uploaded_by_id=None)

    candidates = db_session.query(CompanyCandidate).all()
    assert len(candidates) == 1
    assert candidates[0].reviews_count == 15
    assert candidates[0].rating == 4.9


def test_facets_lists_distinct_region_and_category(db_session):
    data = _wb_bytes([
        ["ООО Дом", "Дома", "Самара", "Самара", "https://dom1.ru", 1, 1, 4.0],
        ["ООО Дом2", "Бани", "Москва", "Москва", "https://dom2.ru", 1, 1, 4.0],
    ])
    import_file(db_session, data, "builders.xlsx", uploaded_by_id=None)
    facets = get_facets(db_session, site_id=1)
    assert set(facets.regions) == {"Самара", "Москва"}
    assert set(facets.categories) == {"Дома", "Бани"}


def test_facets_excludes_pairs_fully_taken_for_site(db_session):
    data = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 1, 1, 4.0]])
    import_file(db_session, data, "builders.xlsx", uploaded_by_id=None)
    candidate = db_session.query(CompanyCandidate).one()
    db_session.add(Company(site_id=1, site_key=candidate.site_key, name="ООО Дом"))
    db_session.commit()

    facets = get_facets(db_session, site_id=1)
    assert "Дома" not in facets.categories
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_companies_imports.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.companies.imports'`

- [ ] **Step 3: Реализовать модуль**

```python
# execution/backend/app/companies/imports.py
"""Импорт xlsx в общий пул кандидатов (upsert по site_key) и выборка facets
для форм создания партии. См. directions/2026-08-06-builders-import-design.md §3."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.companies.import_xlsx import XlsxParseError, parse_workbook
from app.models.company import Company, CompanyCandidate, CompanyImport


def import_file(db: Session, data: bytes, filename: str,
                uploaded_by_id: int | None) -> CompanyImport:
    try:
        rows = parse_workbook(data)
    except XlsxParseError as exc:
        imp = CompanyImport(filename=filename, uploaded_by_id=uploaded_by_id,
                            status="failed", error_message=str(exc))
        db.add(imp)
        db.commit()
        return imp

    imp = CompanyImport(filename=filename, uploaded_by_id=uploaded_by_id,
                        row_count=len(rows), matched_count=len(rows), status="parsed")
    db.add(imp)
    db.flush()

    for row in rows:
        existing = db.scalars(
            select(CompanyCandidate).where(CompanyCandidate.site_key == row.site_key)
        ).first()
        if existing is None:
            existing = CompanyCandidate(site_key=row.site_key)
            db.add(existing)
        existing.website_raw = row.website_raw
        existing.name = row.name
        existing.region_raw = row.region_raw
        existing.category_raw = row.category_raw
        existing.city = row.city
        existing.address = row.address
        existing.phone = row.phone
        existing.email = row.email
        existing.rating = row.rating
        existing.reviews_count = row.reviews_count
        existing.ratings_count = row.ratings_count
        existing.lat = row.lat
        existing.lon = row.lon
        existing.yandex_url = row.yandex_url
        existing.raw_row_json = row.raw_row
        existing.updated_at = utcnow()

    db.commit()
    return imp


@dataclass
class Facets:
    regions: list[str]
    categories: list[str]


def get_facets(db: Session, site_id: int) -> Facets:
    """Различные region_raw/category_raw в пуле, у которых для этого сайта
    есть хотя бы один ещё не взятый кандидат."""
    taken_keys = {
        c.site_key for c in
        db.scalars(select(Company).where(Company.site_id == site_id)).all()
    }
    candidates = db.scalars(select(CompanyCandidate)).all()
    available = [c for c in candidates if c.site_key not in taken_keys]
    regions = sorted({c.region_raw for c in available if c.region_raw})
    categories = sorted({c.category_raw for c in available if c.category_raw})
    return Facets(regions=regions, categories=categories)
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_companies_imports.py -v`
Expected: PASS (4 теста)

- [ ] **Step 5: Commit**

```bash
git add app/companies/imports.py tests/test_companies_imports.py
git commit -m "feat: upsert-импорт кандидатов и facets для партий"
```

---

## Task 6: API импорта xlsx

**Files:**
- Create: `execution/backend/app/api/company_imports.py`
- Modify: `execution/backend/app/main.py`
- Test: `execution/backend/tests/test_api_company_imports.py`

- [ ] **Step 1: Написать падающие тесты**

```python
# execution/backend/tests/test_api_company_imports.py
import io

import openpyxl


def _wb_bytes(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Название", "Категории", "Регион", "Город", "Сайт", "Оценок", "Отзывов", "Рейтинг"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_manager_uploads_import(manager_client):
    data = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 5, 3, 4.5]])
    resp = manager_client.post(
        "/api/company-imports",
        files={"file": ("builders.xlsx", data,
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "parsed"
    assert body["matched_count"] == 1


def test_upload_rejects_broken_file(manager_client):
    resp = manager_client.post(
        "/api/company-imports",
        files={"file": ("bad.xlsx", b"not an xlsx", "application/octet-stream")})
    assert resp.status_code == 200   # ошибка парсинга — это статус, не HTTP-код
    assert resp.json()["status"] == "failed"


def test_facets_endpoint_requires_site_id(manager_client, db_session):
    from app.models.site import Site

    site = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(site)
    db_session.commit()

    data = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 5, 3, 4.5]])
    manager_client.post("/api/company-imports",
                        files={"file": ("b.xlsx", data, "application/octet-stream")})

    resp = manager_client.get(f"/api/company-imports/facets?site_id={site.id}")
    assert resp.status_code == 200
    assert resp.json() == {"regions": ["Самара"], "categories": ["Дома"]}
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_api_company_imports.py -v`
Expected: FAIL — `404 Not Found` (роут не зарегистрирован)

- [ ] **Step 3: Реализовать роутер**

```python
# execution/backend/app/api/company_imports.py
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.companies.imports import get_facets, import_file
from app.models.user import User

router = APIRouter(prefix="/api/company-imports", tags=["company-imports"])


class ImportOut(BaseModel):
    id: int
    filename: str
    row_count: int
    matched_count: int
    error_count: int
    status: str
    error_message: str


class FacetsOut(BaseModel):
    regions: list[str]
    categories: list[str]


@router.post("", response_model=ImportOut)
def upload_import(file: UploadFile = File(...), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    data = file.file.read()
    imp = import_file(db, data, file.filename or "upload.xlsx", uploaded_by_id=user.id)
    return ImportOut(id=imp.id, filename=imp.filename, row_count=imp.row_count,
                     matched_count=imp.matched_count, error_count=imp.error_count,
                     status=imp.status, error_message=imp.error_message)


@router.get("/facets", response_model=FacetsOut)
def facets(site_id: int, db: Session = Depends(get_db),
          _user: User = Depends(get_current_user)):
    result = get_facets(db, site_id)
    return FacetsOut(regions=result.regions, categories=result.categories)
```

- [ ] **Step 4: Зарегистрировать роутер**

```python
# execution/backend/app/main.py — обновить импорт и цикл include_router
from app.api import (
    admin_prompts,
    admin_settings,
    admin_sites,
    admin_users,
    article_batches,
    auth,
    company_imports,
    jobs,
    sites,
    tasks_status,
)
...
for module in (auth, sites, admin_sites, admin_settings, admin_prompts,
               admin_users, article_batches, company_imports, jobs, tasks_status):
    app.include_router(module.router)
```

- [ ] **Step 5: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_api_company_imports.py -v`
Expected: PASS (3 теста)

- [ ] **Step 6: Commit**

```bash
git add app/api/company_imports.py app/main.py tests/test_api_company_imports.py
git commit -m "feat: API загрузки xlsx и facets"
```

---

## Task 7: Отбор партии

**Files:**
- Create: `execution/backend/app/companies/selection.py`
- Test: `execution/backend/tests/test_companies_selection.py`

- [ ] **Step 1: Написать падающие тесты**

```python
# execution/backend/tests/test_companies_selection.py
from app.companies.selection import add_next_candidate, select_candidates
from app.models.company import Company, CompanyCandidate


def _candidate(db, **over):
    defaults = dict(site_key="a.ru", name="А", region_raw="Самара",
                    category_raw="Дома", reviews_count=1)
    defaults.update(over)
    c = CompanyCandidate(**defaults)
    db.add(c)
    db.commit()
    return c


def test_select_filters_by_region_and_category(db_session):
    _candidate(db_session, site_key="a.ru", region_raw="Самара", category_raw="Дома")
    _candidate(db_session, site_key="b.ru", region_raw="Москва", category_raw="Дома")
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=10)
    assert [c.site_key for c in result] == ["a.ru"]


def test_select_sorts_by_reviews_desc(db_session):
    _candidate(db_session, site_key="a.ru", reviews_count=3)
    _candidate(db_session, site_key="b.ru", reviews_count=10)
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=10)
    assert [c.site_key for c in result] == ["b.ru", "a.ru"]


def test_select_respects_count(db_session):
    for i in range(5):
        _candidate(db_session, site_key=f"{i}.ru", reviews_count=i)
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=2)
    assert len(result) == 2


def test_select_excludes_candidates_already_taken_for_site(db_session):
    _candidate(db_session, site_key="a.ru")
    db_session.add(Company(site_id=1, site_key="a.ru", name="А"))
    db_session.commit()
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=10)
    assert result == []


def test_select_allows_same_candidate_on_different_site(db_session):
    _candidate(db_session, site_key="a.ru")
    db_session.add(Company(site_id=2, site_key="a.ru", name="А"))
    db_session.commit()
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=10)
    assert [c.site_key for c in result] == ["a.ru"]


def test_add_next_candidate_skips_already_in_batch(db_session):
    _candidate(db_session, site_key="a.ru", reviews_count=10)
    _candidate(db_session, site_key="b.ru", reviews_count=5)
    taken_site_keys = {"a.ru"}
    excluded_site_keys: set[str] = set()
    next_candidate = add_next_candidate(
        db_session, site_id=1, region_raw="Самара", category_raw="Дома",
        already_in_batch=taken_site_keys, excluded=excluded_site_keys)
    assert next_candidate.site_key == "b.ru"


def test_add_next_candidate_returns_none_when_exhausted(db_session):
    _candidate(db_session, site_key="a.ru")
    result = add_next_candidate(
        db_session, site_id=1, region_raw="Самара", category_raw="Дома",
        already_in_batch={"a.ru"}, excluded=set())
    assert result is None
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_companies_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.companies.selection'`

- [ ] **Step 3: Реализовать модуль**

```python
# execution/backend/app/companies/selection.py
"""Отбор кандидатов в партию: фильтр региона+категории, исключение уже
взятых для сайта, сортировка по отзывам. См. design doc §4."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company, CompanyCandidate


def _taken_site_keys(db: Session, site_id: int) -> set[str]:
    return {c.site_key for c in
           db.scalars(select(Company).where(Company.site_id == site_id)).all()}


def select_candidates(db: Session, site_id: int, region_raw: str, category_raw: str,
                      count: int) -> list[CompanyCandidate]:
    taken = _taken_site_keys(db, site_id)
    matching = db.scalars(
        select(CompanyCandidate).where(
            CompanyCandidate.region_raw == region_raw,
            CompanyCandidate.category_raw == category_raw,
        )
    ).all()
    available = [c for c in matching if c.site_key not in taken]
    available.sort(key=lambda c: -c.reviews_count)
    return available[:count]


def add_next_candidate(db: Session, site_id: int, region_raw: str, category_raw: str,
                       already_in_batch: set[str], excluded: set[str]) -> CompanyCandidate | None:
    """Следующий по рейтингу кандидат, не входящий ни в партию, ни в список
    вычеркнутых менеджером, ни уже взятый для сайта где-либо ещё."""
    taken = _taken_site_keys(db, site_id)
    matching = db.scalars(
        select(CompanyCandidate).where(
            CompanyCandidate.region_raw == region_raw,
            CompanyCandidate.category_raw == category_raw,
        )
    ).all()
    skip = taken | already_in_batch | excluded
    available = [c for c in matching if c.site_key not in skip]
    if not available:
        return None
    return max(available, key=lambda c: c.reviews_count)
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_companies_selection.py -v`
Expected: PASS (7 тестов)

- [ ] **Step 5: Commit**

```bash
git add app/companies/selection.py tests/test_companies_selection.py
git commit -m "feat: отбор кандидатов партии с дедупом по сайту"
```

---

## Task 8: API партий — создание, превью, вычёркивание, добор

**Files:**
- Create: `execution/backend/app/api/company_batches.py`
- Modify: `execution/backend/app/main.py`
- Test: `execution/backend/tests/test_api_company_batches.py`

- [ ] **Step 1: Написать падающие тесты**

```python
# execution/backend/tests/test_api_company_batches.py
import pytest

from app.models.company import CompanyCandidate
from app.models.site import Site


@pytest.fixture
def site_id(db_session):
    site = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(site)
    db_session.commit()
    return site.id


@pytest.fixture
def candidates(db_session):
    db_session.add_all([
        CompanyCandidate(site_key="a.ru", name="А", region_raw="Самара",
                         category_raw="Дома", reviews_count=10),
        CompanyCandidate(site_key="b.ru", name="Б", region_raw="Самара",
                         category_raw="Дома", reviews_count=5),
    ])
    db_session.commit()


def test_create_batch_selects_top_candidates(manager_client, site_id, candidates):
    resp = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "selection_review"
    assert [c["name"] for c in body["companies"]] == ["А", "Б"]


def test_remove_company_from_batch(manager_client, site_id, candidates):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    company_id = batch["companies"][0]["id"]

    resp = manager_client.delete(f"/api/company-batches/{batch['id']}/companies/{company_id}")
    assert resp.status_code == 200
    assert len(resp.json()["companies"]) == 1


def test_add_next_after_removal(manager_client, site_id, db_session):
    db_session.add_all([
        CompanyCandidate(site_key="a.ru", name="А", region_raw="Самара",
                         category_raw="Дома", reviews_count=10),
        CompanyCandidate(site_key="b.ru", name="Б", region_raw="Самара",
                         category_raw="Дома", reviews_count=5),
        CompanyCandidate(site_key="c.ru", name="В", region_raw="Самара",
                         category_raw="Дома", reviews_count=1),
    ])
    db_session.commit()
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    company_id = batch["companies"][1]["id"]   # «Б», reviews=5
    manager_client.delete(f"/api/company-batches/{batch['id']}/companies/{company_id}")

    resp = manager_client.post(f"/api/company-batches/{batch['id']}/companies/next")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["companies"]]
    assert names == ["А", "В"]   # добрали «В» (следующая по рейтингу после «Б»)


def test_add_next_returns_400_when_nothing_left(manager_client, site_id, candidates):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    resp = manager_client.post(f"/api/company-batches/{batch['id']}/companies/next")
    assert resp.status_code == 400
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_api_company_batches.py -v`
Expected: FAIL — `404 Not Found`

- [ ] **Step 3: Реализовать роутер**

```python
# execution/backend/app/api/company_batches.py
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.companies.selection import add_next_candidate, select_candidates
from app.models.company import Company, CompanyBatch
from app.models.site import Site
from app.models.user import User

router = APIRouter(prefix="/api", tags=["companies"])


class BatchIn(BaseModel):
    site_id: int
    region_raw: str
    category_raw: str
    category_normalized: str
    teaser_category_id: int
    teaser_city_id: int
    teaser_location_id: int
    count: int = Field(ge=1, le=50)


class CompanyOut(BaseModel):
    id: int
    name: str
    website: str
    region: str
    rating: float | None
    reviews_count: int
    status: str
    remote_url: str
    error_text: str


class BatchOut(BaseModel):
    id: int
    site_id: int
    site_name: str
    region_raw: str
    category_raw: str
    category_normalized: str
    requested_count: int
    status: str
    error_text: str
    created_at: datetime
    companies: list[CompanyOut] = []


def _to_out(db: Session, batch: CompanyBatch) -> BatchOut:
    site = db.get(Site, batch.site_id)
    return BatchOut(
        id=batch.id, site_id=batch.site_id, site_name=site.name if site else "—",
        region_raw=batch.region_raw, category_raw=batch.category_raw,
        category_normalized=batch.category_normalized,
        requested_count=batch.requested_count, status=batch.status,
        error_text=batch.error_text, created_at=batch.created_at,
        companies=[CompanyOut(id=c.id, name=c.name, website=c.website, region=c.region,
                              rating=c.rating, reviews_count=c.reviews_count,
                              status=c.status, remote_url=c.remote_url,
                              error_text=c.error_text)
                  for c in batch.companies],
    )


def _get_or_404(db: Session, batch_id: int) -> CompanyBatch:
    batch = db.get(CompanyBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "партия не найдена")
    return batch


def _company_from_candidate(batch: CompanyBatch, candidate) -> Company:
    return Company(
        site_id=batch.site_id, batch_id=batch.id, candidate_id=candidate.id,
        site_key=candidate.site_key, website=candidate.website_raw, name=candidate.name,
        region=candidate.region_raw, category_normalized=batch.category_normalized,
        rating=candidate.rating, reviews_count=candidate.reviews_count,
        yandex_url=candidate.yandex_url,
    )


@router.post("/company-batches", response_model=BatchOut)
def create_batch(payload: BatchIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    site = db.get(Site, payload.site_id)
    if site is None:
        raise HTTPException(404, "сайт не найден")
    if not site.is_active:
        raise HTTPException(400, "сайт деактивирован — создание партий недоступно")

    batch = CompanyBatch(
        site_id=payload.site_id, region_raw=payload.region_raw,
        category_raw=payload.category_raw, category_normalized=payload.category_normalized,
        teaser_category_id=payload.teaser_category_id,
        teaser_city_id=payload.teaser_city_id, teaser_location_id=payload.teaser_location_id,
        requested_count=payload.count, created_by_id=user.id,
    )
    db.add(batch)
    db.flush()

    candidates = select_candidates(db, payload.site_id, payload.region_raw,
                                   payload.category_raw, payload.count)
    for candidate in candidates:
        db.add(_company_from_candidate(batch, candidate))
    db.commit()
    return _to_out(db, batch)


@router.get("/company-batches", response_model=list[BatchOut])
def list_batches(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    batches = db.query(CompanyBatch).order_by(CompanyBatch.id.desc()).all()
    return [_to_out(db, b) for b in batches]


@router.get("/company-batches/{batch_id}", response_model=BatchOut)
def read_batch(batch_id: int, db: Session = Depends(get_db),
              _user: User = Depends(get_current_user)):
    return _to_out(db, _get_or_404(db, batch_id))


@router.delete("/company-batches/{batch_id}/companies/{company_id}", response_model=BatchOut)
def remove_company(batch_id: int, company_id: int, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if batch.status != "selection_review":
        raise HTTPException(400, "партия уже запущена — правка списка недоступна")
    company = next((c for c in batch.companies if c.id == company_id), None)
    if company is None:
        raise HTTPException(404, "компания не найдена в этой партии")
    db.delete(company)
    db.commit()
    db.refresh(batch)
    return _to_out(db, batch)


@router.post("/company-batches/{batch_id}/companies/next", response_model=BatchOut)
def add_next(batch_id: int, db: Session = Depends(get_db),
            _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if batch.status != "selection_review":
        raise HTTPException(400, "партия уже запущена — правка списка недоступна")
    already = {c.site_key for c in batch.companies}
    candidate = add_next_candidate(db, batch.site_id, batch.region_raw, batch.category_raw,
                                   already_in_batch=already, excluded=set())
    if candidate is None:
        raise HTTPException(400, "больше подходящих компаний не найдено")
    db.add(_company_from_candidate(batch, candidate))
    db.commit()
    db.refresh(batch)
    return _to_out(db, batch)
```

- [ ] **Step 4: Зарегистрировать роутер**

```python
# execution/backend/app/main.py
from app.api import (
    admin_prompts,
    admin_settings,
    admin_sites,
    admin_users,
    article_batches,
    auth,
    company_batches,
    company_imports,
    jobs,
    sites,
    tasks_status,
)
...
for module in (auth, sites, admin_sites, admin_settings, admin_prompts,
               admin_users, article_batches, company_imports, company_batches,
               jobs, tasks_status):
    app.include_router(module.router)
```

- [ ] **Step 5: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_api_company_batches.py -v`
Expected: PASS (4 теста)

- [ ] **Step 6: Commit**

```bash
git add app/api/company_batches.py app/main.py tests/test_api_company_batches.py
git commit -m "feat: API партий строителей — создание, превью, добор"
```

---

## Task 9: Скрейпинг сайта компании

**Files:**
- Create: `execution/backend/app/companies/scrape.py`
- Test: `execution/backend/tests/test_companies_scrape.py`

Портирует `fetch_html`/`clean_to_text` из `execution/step2_scrape_company.py`.

- [ ] **Step 1: Написать падающие тесты**

```python
# execution/backend/tests/test_companies_scrape.py
from unittest.mock import Mock, patch

import pytest
import requests

from app.companies.scrape import ScrapeError, fetch_company_text


def test_fetch_company_text_extracts_readable_text():
    html = "<html><head><title>ООО Дом</title><style>x{}</style></head>" \
          "<body><script>1</script><p>Строим дома под ключ.</p></body></html>"
    response = Mock(text=html)
    response.raise_for_status = Mock()
    with patch("app.companies.scrape.requests.get", return_value=response):
        text = fetch_company_text("https://dom.ru")
    assert "TITLE: ООО Дом" in text
    assert "Строим дома под ключ." in text
    assert "1" not in text.split("\n")   # содержимое <script> вырезано


def test_fetch_company_text_truncates_to_limit():
    html = "<html><body><p>" + "а" * 20_000 + "</p></body></html>"
    response = Mock(text=html)
    response.raise_for_status = Mock()
    with patch("app.companies.scrape.requests.get", return_value=response):
        text = fetch_company_text("https://dom.ru")
    assert len(text) <= 12_100   # запас на "TITLE: \n\n"


def test_fetch_company_text_wraps_network_error():
    with patch("app.companies.scrape.requests.get",
              side_effect=requests.ConnectionError("boom")):
        with pytest.raises(ScrapeError):
            fetch_company_text("https://dom.ru")
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_companies_scrape.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.companies.scrape'`

- [ ] **Step 3: Реализовать модуль**

```python
# execution/backend/app/companies/scrape.py
"""Скачивание и очистка текста сайта компании — портирует
execution/step2_scrape_company.py (fetch_html/clean_to_text)."""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT_SECONDS = 12
TEXT_LIMIT_CHARS = 12_000

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


class ScrapeError(RuntimeError):
    pass


def _clean_to_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "header", "footer"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return f"TITLE: {title}\n\n" + "\n".join(lines)[:TEXT_LIMIT_CHARS]


def fetch_company_text(url: str) -> str:
    try:
        response = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS,
                                allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError(f"не удалось загрузить {url}: {exc}") from exc
    return _clean_to_text(response.text)
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_companies_scrape.py -v`
Expected: PASS (3 теста)

- [ ] **Step 5: Commit**

```bash
git add app/companies/scrape.py tests/test_companies_scrape.py
git commit -m "feat: скрейпинг текста сайта компании"
```

---

## Task 10: Промпт `builder_text`

**Files:**
- Modify: `execution/backend/app/ai/prompts.py`
- Modify: `execution/backend/app/seed.py`
- Test: `execution/backend/tests/test_ai_prompts.py`

- [ ] **Step 1: Дописать падающий тест**

```python
# добавить в execution/backend/tests/test_ai_prompts.py
def test_builder_text_prompt_variables_declared():
    from app.ai.prompts import PROMPT_KEYS, PROMPT_VARIABLES

    assert "builder_text" in PROMPT_KEYS
    assert PROMPT_VARIABLES["builder_text"] == frozenset(
        {"company_name", "city", "category", "site_name", "tone_of_voice", "scraped_text"})
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_ai_prompts.py -k builder_text -v`
Expected: FAIL — `KeyError: 'builder_text'`

- [ ] **Step 3: Добавить ключ и переменные**

```python
# execution/backend/app/ai/prompts.py — заменить
PROMPT_KEYS = ("topics", "article_body", "cover", "content_image")
# на
PROMPT_KEYS = ("topics", "article_body", "cover", "content_image", "builder_text")
```

```python
# execution/backend/app/ai/prompts.py — добавить в PROMPT_VARIABLES
    "builder_text": frozenset({"company_name", "city", "category", "site_name",
                               "tone_of_voice", "scraped_text"}),
```

- [ ] **Step 4: Добавить дефолтный промпт в seed**

```python
# execution/backend/app/seed.py — добавить в DEFAULT_PROMPTS
    "builder_text": """Напиши маркетинговый текст о компании «{{ company_name }}»
({{ category }}, {{ city }}) для страницы на сайте «{{ site_name }}».

Тон материала: {{ tone_of_voice }}

Текст сайта компании (сырой, может содержать мусор навигации):
{{ scraped_text }}

Требования:
- используй факты из текста сайта, не выдумывай услуги и проекты, которых там нет;
- никаких цен, контактов и юридических реквизитов — они уже есть на странице отдельно;
- деловой, но не рекламный тон, без превосходной степени без оснований.

Верни СТРОГО JSON-объект с четырьмя полями (каждое — 2-4 предложения):
{"about_company": "...", "specialization": "...", "projects_services": "...",
 "benefits": "..."}""",
```

- [ ] **Step 5: Запустить тест и убедиться, что он проходит**

Run: `pytest tests/test_ai_prompts.py -k builder_text -v`
Expected: PASS

- [ ] **Step 6: Прогнать полный набор тестов промптов (регрессия)**

Run: `pytest tests/test_ai_prompts.py tests/test_settings.py -v`
Expected: PASS — существующий тест `test_default_prompts_render_with_real_contexts`
(если он есть) должен пройти и для нового ключа; если тест жёстко перечисляет
ключи, дополни его список так же, как `PROMPT_KEYS`.

- [ ] **Step 7: Commit**

```bash
git add app/ai/prompts.py app/seed.py tests/test_ai_prompts.py
git commit -m "feat: промпт builder_text для генерации текста о компании"
```

---

## Task 11: Заполнение шаблона builder_template_html

**Files:**
- Create: `execution/backend/app/companies/template.py`
- Test: `execution/backend/tests/test_companies_template.py`

Портирует `fill_html` из `execution/step3_fill_template.py`, но принимает
шаблон строкой (из `site.builder_template_html`), а не читает файл с диска,
и не занимается локализацией логотипа (это отдельный шаг в Task 13).

- [ ] **Step 1: Написать падающие тесты**

```python
# execution/backend/tests/test_companies_template.py
from app.companies.template import fill_builder_template

TEMPLATE = """
<div id="builder">
  <img id="builder-logo" src="" alt="">
  <span id="builder-logo-text"></span>
  <h1 id="builder-main-title"></h1>
  <div id="builder-about-company"><p></p></div>
  <div id="builder-specialization"><p></p></div>
  <div id="builder-contacts">
    <h2 id="builder-contacts-title"></h2>
    <div id="builder-contacts-grid">
      <div id="builder-contact-1">
        <a class="builder-line-address"><span class="circle-img"></span><p></p></a>
        <a class="builder-line-phone"><span class="circle-img"></span></a>
        <a class="builder-line-email"><span class="circle-img"></span></a>
        <a class="builder-line-time"><span class="circle-img"></span><p></p></a>
        <a class="builder-line-site"><span class="circle-img"></span></a>
        <a class="builder-line-note"><span class="circle-img"></span><p></p></a>
      </div>
    </div>
  </div>
</div>
"""


def _info(**over):
    base = dict(
        builder_name="ООО Дом", city_name="Самара", city_prepositional="Самаре",
        builder_logo_src="", builder_logo_alt="", about_company="Строим дома.",
        specialization="", projects_services="", benefits="",
        contacts=[{"address": "ул. Ленина 1", "phone_tel": "+78462770605",
                  "phone_text": "+7 846 277-06-05", "email": "info@dom.ru",
                  "working_hours": "9:00-18:00", "site_url": "https://dom.ru",
                  "site_text": "dom.ru"}],
        address="ул. Ленина 1", coordinates="",
    )
    base.update(over)
    return base


def test_fill_sets_title_and_about():
    html = fill_builder_template(TEMPLATE, _info())
    assert 'id="builder-main-title"' in html
    assert "О компании ООО Дом" in html
    assert "Строим дома." in html


def test_fill_drops_empty_about_block():
    html = fill_builder_template(TEMPLATE, _info(specialization=""))
    assert 'id="builder-specialization"' not in html


def test_fill_renders_contact_line():
    html = fill_builder_template(TEMPLATE, _info())
    assert 'href="tel:+78462770605"' in html
    assert "+7 846 277-06-05" in html
    assert 'href="mailto:info@dom.ru"' in html


def test_fill_uses_text_logo_fallback_when_no_logo_src():
    html = fill_builder_template(TEMPLATE, _info(builder_logo_src=""))
    assert "ООО Дом" in html
    assert 'id="builder-logo"' not in html   # img-логотип убран
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_companies_template.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.companies.template'`

- [ ] **Step 3: Реализовать модуль**

Портирует `fill_html`/`set_text` из `execution/step3_fill_template.py` дословно
(логика уже проверена в бою), убирая параметры `target_site`/`localize_logos`
(локализация логотипа переезжает в `app/companies/builder.py`, Task 13).

```python
# execution/backend/app/companies/template.py
"""Заполнение builder_template_html данными компании. Портирует
fill_html из execution/step3_fill_template.py — та же разметка-контракт
(id/класс атрибуты шаблона), без локализации логотипа."""

from __future__ import annotations

import json

from bs4 import BeautifulSoup, Comment, NavigableString


def _remove_comments(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()


def _set_text(tag, text: str) -> None:
    tag.clear()
    tag.append(NavigableString(text))


def fill_builder_template(template: str, info: dict) -> str:
    soup = BeautifulSoup(template, "html.parser")
    _remove_comments(soup)

    name = (info.get("builder_name") or "").strip()
    city_prep = (info.get("city_prepositional") or info.get("city_name") or "").strip()
    logo_src = (info.get("builder_logo_src") or "").strip()
    logo_alt = (info.get("builder_logo_alt") or name).strip()
    about = (info.get("about_company") or "").strip()
    spec = (info.get("specialization") or "").strip()
    projects = (info.get("projects_services") or "").strip()
    benefits = (info.get("benefits") or "").strip()

    contacts = info.get("contacts") or []
    if isinstance(contacts, str):
        try:
            contacts = json.loads(contacts)
        except json.JSONDecodeError:
            contacts = []
    if not contacts:
        contacts = [{}]

    logo_img = soup.find(id="builder-logo")
    logo_text_span = soup.find(id="builder-logo-text")
    if logo_src:
        if logo_img:
            logo_img["src"] = logo_src
            logo_img["alt"] = logo_alt
        if logo_text_span:
            logo_text_span.decompose()
    else:
        if logo_img:
            logo_img.decompose()
        if logo_text_span:
            _set_text(logo_text_span, name)

    main_title = soup.find(id="builder-main-title")
    if main_title:
        _set_text(main_title, f"О компании {name}")

    def fill_about(block_id: str, text: str) -> None:
        block = soup.find(id=block_id)
        if not block:
            return
        if text:
            p = block.find("p")
            if p:
                p.clear()
                for i, para in enumerate(text.split("\n\n")):
                    para = para.strip()
                    if not para:
                        continue
                    if i == 0:
                        p.append(NavigableString(para))
                    else:
                        new_p = soup.new_tag("p")
                        new_p.append(NavigableString(para))
                        block.append(new_p)
        else:
            block.decompose()

    fill_about("builder-about-company", about)
    fill_about("builder-specialization", spec)
    fill_about("builder-projects-services", projects)
    fill_about("builder-benefits", benefits)

    contacts_div = soup.find(id="builder-contacts")
    if contacts_div:
        title = contacts_div.find(id="builder-contacts-title")
        if title:
            prep = city_prep
            if prep.lower().startswith(("в ", "во ")):
                _set_text(title, f"{name} {prep}")
            else:
                _set_text(title, f"{name} в {prep}")

        grid = contacts_div.find(id="builder-contacts-grid")
        if grid:
            tpl_item = grid.find("div", id="builder-contact-1")

            def rebuild_anchor(el, href: str, text: str) -> None:
                el["href"] = href
                circle = el.find(class_="circle-img")
                circle_copy = BeautifulSoup(str(circle), "html.parser") if circle else None
                el.clear()
                if circle_copy:
                    el.append(circle_copy)
                el.append(NavigableString(f"\n        {text}\n      "))

            items_html = []
            for idx, c in enumerate(contacts, start=1):
                addr = (c.get("address") or "").strip()
                phone_tel = (c.get("phone_tel") or "").strip()
                phone_text_val = (c.get("phone_text") or phone_tel).strip()
                email_val = (c.get("email") or "").strip()
                hours = (c.get("working_hours") or "").strip()
                site_url = (c.get("site_url") or "").strip()
                site_text_val = (c.get("site_text") or site_url).strip()
                note = (c.get("note") or "").strip()

                if not any([addr, phone_tel, email_val, hours, site_url]):
                    continue
                if tpl_item is None:
                    continue

                item = BeautifulSoup(str(tpl_item), "html.parser")
                item_div = item.find("div")
                item_div["id"] = f"builder-contact-{idx}"

                def line(cls_fragment, keep, mutate=None):
                    el = item_div.find(class_=lambda c: c and cls_fragment in c)
                    if el:
                        if keep and mutate:
                            mutate(el)
                        elif not keep:
                            el.decompose()

                line("builder-line-address", bool(addr),
                    lambda el: _set_text(el.find("p"), addr) if el.find("p") else None)
                line("builder-line-phone", bool(phone_tel),
                    lambda el: rebuild_anchor(el, f"tel:{phone_tel}", phone_text_val))
                line("builder-line-email", bool(email_val),
                    lambda el: rebuild_anchor(el, f"mailto:{email_val}", email_val))
                line("builder-line-time", bool(hours),
                    lambda el: _set_text(el.find("p"), hours) if el.find("p") else None)
                line("builder-line-site", bool(site_url),
                    lambda el: rebuild_anchor(el, site_url, site_text_val))
                line("builder-line-note", bool(note),
                    lambda el: _set_text(el.find("p"), note) if el.find("p") else None)

                items_html.append(str(item_div))

            grid.clear()
            for h in items_html:
                grid.append(BeautifulSoup(h, "html.parser"))

    return str(soup)
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_companies_template.py -v`
Expected: PASS (4 теста)

- [ ] **Step 5: Commit**

```bash
git add app/companies/template.py tests/test_companies_template.py
git commit -m "feat: заполнение builder_template_html данными компании"
```

---

## Task 12: SiteClient — создание карточки-тизера

**Files:**
- Modify: `execution/backend/app/sites/client.py`
- Test: `execution/backend/tests/test_sites_client.py`

Портирует `build_teaser_payload`/`create_teaser` из `execution/step6_manage_teasers.py`.

- [ ] **Step 1: Дописать падающий тест**

```python
# добавить в execution/backend/tests/test_sites_client.py
from unittest.mock import Mock, patch

from app.sites.client import SiteClient


def test_create_teaser_posts_expected_payload():
    client = SiteClient("https://s.ru", "tok")
    response = Mock(ok=True, status_code=201)
    response.json.return_value = {"id": 42}
    with patch("app.sites.client.requests.post", return_value=response) as post:
        teaser_id = client.create_teaser(
            name="ООО Дом", slug="ooo-dom-samara", address="ул. Ленина 1",
            phone="79991234567", email="info@dom.ru", website="https://dom.ru",
            page_url="/s/ooo-dom-samara/", category=3, city=1, location=1,
        )
    assert teaser_id == 42
    payload = post.call_args.kwargs["json"]
    assert payload["slug"] == "ooo-dom-samara"
    assert payload["category"] == 3
    assert payload["is_active"] is False


def test_create_teaser_raises_on_error():
    from app.sites.client import SiteAPIError

    client = SiteClient("https://s.ru", "tok")
    response = Mock(ok=False, status_code=400, text="bad request")
    with patch("app.sites.client.requests.post", return_value=response):
        try:
            client.create_teaser(
                name="А", slug="a", address="", phone="", email="", website="",
                page_url="/s/a/", category=1, city=1, location=1,
            )
            assert False, "ожидался SiteAPIError"
        except SiteAPIError:
            pass
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_sites_client.py -k teaser -v`
Expected: FAIL — `AttributeError: 'SiteClient' object has no attribute 'create_teaser'`

- [ ] **Step 3: Добавить метод**

```python
# execution/backend/app/sites/client.py — добавить константу рядом с ARTICLE_IMG_DIR
ADDRESSES_SERVICES_PATH = "/api/v1/addresses-services/"
```

```python
# execution/backend/app/sites/client.py — добавить метод в класс SiteClient,
# после set_page_cover
    def create_teaser(self, name: str, slug: str, address: str, phone: str, email: str,
                      website: str, page_url: str, category: int, city: int,
                      location: int) -> int:
        """Карточка-тизер услуги — /api/v1/addresses-services/, не обложка
        страницы. is_active=False: включает менеджер вручную, симметрично
        published=False у create_page."""
        payload = {
            "name": name, "slug": slug, "address": address, "phone": phone,
            "email": email, "website": website, "page_url": page_url,
            "is_active": False, "location": location, "category": category, "city": city,
        }
        response = self._check(
            requests.post(f"{self.base_url}{ADDRESSES_SERVICES_PATH}", json=payload,
                          headers={**self._headers, "Content-Type": "application/json"},
                          timeout=self.timeout),
            "создание тизера")
        return self._json(response, "создание тизера")["id"]
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_sites_client.py -v`
Expected: PASS (все тесты файла, включая 2 новых)

- [ ] **Step 5: Commit**

```bash
git add app/sites/client.py tests/test_sites_client.py
git commit -m "feat: SiteClient.create_teaser"
```

---

## Task 13: Сборка одной компании

**Files:**
- Create: `execution/backend/app/companies/builder.py`
- Test: `execution/backend/tests/test_companies_builder.py`

Аналог `app/articles/builder.py`: скрейпинг → RouterAI → шаблон → страница →
логотип → тизер, с падением по шагам в `company.status = "failed"`.

- [ ] **Step 1: Написать падающие тесты**

```python
# execution/backend/tests/test_companies_builder.py
from unittest.mock import Mock

import pytest

from app.ai.text import LLMError, TextResult
from app.companies.builder import CompanyBuilder, logo_filename, slug_for_company
from app.companies.scrape import ScrapeError
from app.models.company import Company, CompanyBatch, CompanyInfo
from app.models.site import Site


@pytest.fixture
def site():
    return Site(id=1, name="С", domain="s.ru", base_url="https://s.ru",
               builder_template_html="<div id=\"builder\"><h1 id=\"builder-main-title\">"
                                     "</h1></div>",
               builder_parent_id=10, tone_of_voice="деловой")


@pytest.fixture
def batch():
    return CompanyBatch(id=1, site_id=1, category_normalized="Дома под ключ",
                        teaser_category_id=3, teaser_city_id=1, teaser_location_id=1,
                        requested_count=1)


@pytest.fixture
def company(batch):
    c = Company(id=7, site_id=1, batch_id=batch.id, site_key="dom.ru",
               website="https://dom.ru", name="ООО Дом", region="Самара")
    c.batch = batch
    return c


def _builder(db, company, site, text_client=None, site_client=None, scrape=None):
    return CompanyBuilder(
        db=db, company=company, site=site,
        text_client=text_client or Mock(complete_json=Mock(return_value=TextResult(
            data={"about_company": "Строим дома.", "specialization": "Каркасные дома.",
                 "projects_services": "50 проектов.", "benefits": "Гарантия 5 лет."},
            tokens_prompt=10, tokens_completion=20, cost=0.01))),
        site_client=site_client or Mock(
            create_page=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
            create_teaser=Mock(return_value=555),
            upload_file=Mock(return_value="/media/uploads/service-img/cp-company-7-logo.webp"),
        ),
        scrape_fn=scrape or Mock(return_value="TITLE: ООО Дом\n\nСтроим дома под ключ."),
        job_run_id=None,
    )


def test_logo_filename_uses_company_prefix():
    assert logo_filename(7) == "cp-company-7-logo.webp"


def test_slug_for_company_transliterates_name_and_city():
    assert slug_for_company("ООО Дом", "Самара") == "ooo-dom-samara"


def test_build_success_publishes_company(db_session, site, company):
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", city_prepositional="Самаре",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    builder = _builder(db_session, company, site)
    builder.build()

    assert company.status == "published"
    assert company.remote_page_id == 99
    assert company.teaser_id == 555
    assert company.info.about_company == "Строим дома."
    assert "Строим дома под ключ" in company.info.scraped_text


def test_build_fails_company_on_scrape_error(db_session, site, company):
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом"))
    db_session.commit()

    builder = _builder(db_session, company, site,
                       scrape=Mock(side_effect=ScrapeError("нет ответа")))
    builder.build()

    assert company.status == "failed"
    assert "нет ответа" in company.error_text


def test_build_fails_company_on_llm_error(db_session, site, company):
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом"))
    db_session.commit()

    text_client = Mock(complete_json=Mock(side_effect=LLMError("модель недоступна")))
    builder = _builder(db_session, company, site, text_client=text_client)
    builder.build()

    assert company.status == "failed"
    assert "модель недоступна" in company.error_text


def test_build_requires_builder_template(db_session, company):
    site = Site(id=1, name="С", domain="s.ru", base_url="https://s.ru",
               builder_template_html="", builder_parent_id=10)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом"))
    db_session.commit()

    builder = _builder(db_session, company, site)
    builder.build()

    assert company.status == "failed"
    assert "шаблон" in company.error_text.lower()
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_companies_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.companies.builder'`

- [ ] **Step 3: Реализовать модуль**

```python
# execution/backend/app/companies/builder.py
"""Сборка одной компании: скрейпинг → RouterAI → шаблон → страница →
логотип → тизер. Аналог app/articles/builder.py — падение на любом шаге
переводит компанию в status="failed", а не роняет всю партию."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.factory import build_text_client
from app.ai.prompts import PromptError, render_prompt, resolve_prompt
from app.ai.text import LLMError
from app.companies.scrape import ScrapeError, fetch_company_text
from app.companies.template import fill_builder_template
from app.models.company import YANDEX_INFO_FIELDS, Company, CompanyInfo
from app.models.job import LlmUsage
from app.models.site import Site
from app.sites.client import SERVICE_IMG_DIR, SiteAPIError, slugify

AI_TEXT_FIELDS = ("about_company", "specialization", "projects_services", "benefits")


def logo_filename(company_id: int) -> str:
    """Префикс cp-company- — та же причина, что и у cp-article- в
    app/articles/builder.py: не пересекаться со старой CLI-схемой
    (execution/step3_fill_template.py грузит логотипы как logo-{name})."""
    return f"cp-company-{company_id}-logo.webp"


def slug_for_company(name: str, city: str) -> str:
    return slugify(f"{name} {city}", limit=60)


class CompanyBuilder:
    def __init__(self, db: Session, company: Company, site: Site, text_client,
                site_client, scrape_fn=fetch_company_text, job_run_id: int | None = None):
        self.db = db
        self.company = company
        self.site = site
        self.text_client = text_client
        self.site_client = site_client
        self.scrape_fn = scrape_fn
        self.job_run_id = job_run_id

    def build(self) -> None:
        self.company.status = "generating"
        self.db.commit()
        try:
            self._require_template()
            info = self._require_info()
            scraped_text = self._scrape()
            ai_fields = self._generate_text(info, scraped_text)
            self._apply_ai_fields(info, ai_fields, scraped_text)
            self._relocate_logo(info)
            html = fill_builder_template(self.site.builder_template_html, self._info_dict(info))
            page = self._create_page(info, html)
            self._create_teaser(info, page)
        except (ScrapeError, LLMError, PromptError, SiteAPIError) as exc:
            self.db.rollback()
            self.company.status = "failed"
            self.company.error_text = str(exc)
            self.db.commit()
            return
        self.company.status = "published"
        self.company.error_text = ""
        self.db.commit()

    def _require_template(self) -> None:
        if not self.site.builder_template_html:
            raise SiteAPIError(
                "у сайта не задан шаблон карточки строителя — заполни "
                "builder_template_html на карточке сайта")

    def _require_info(self) -> CompanyInfo:
        info = self.company.info
        if info is None:
            raise SiteAPIError(
                "у компании нет данных Яндекс.Карт (company_info) — "
                "партия создана некорректно")
        return info

    def _scrape(self) -> str:
        return self.scrape_fn(self.company.website)

    def _generate_text(self, info: CompanyInfo, scraped_text: str) -> dict:
        template = resolve_prompt(self.db, "builder_text", self.site.id)
        prompt = render_prompt(template, {
            "company_name": info.builder_name or self.company.name,
            "city": info.city_name or self.company.region,
            "category": self.company.category_normalized,
            "site_name": self.site.name,
            "tone_of_voice": self.site.tone_of_voice,
            "scraped_text": scraped_text,
        })
        result = self.text_client.complete_json(prompt)
        self._record_usage(result.tokens_prompt, result.tokens_completion, result.cost)
        if not isinstance(result.data, dict) or not all(k in result.data for k in AI_TEXT_FIELDS):
            raise LLMError("модель вернула объект без всех текстовых полей")
        return result.data

    def _apply_ai_fields(self, info: CompanyInfo, ai_fields: dict, scraped_text: str) -> None:
        # YANDEX_INFO_FIELDS не трогаются — только четыре текстовых поля.
        for field in AI_TEXT_FIELDS:
            setattr(info, field, ai_fields[field])
        # Сырой текст сайта — для отладки качества промпта, аналог raw_html
        # в старой схеме (execution/db.py). Ничего не читает его обратно.
        info.scraped_text = scraped_text
        self.db.commit()

    def _relocate_logo(self, info: CompanyInfo) -> None:
        """Внешний логотип перезаливается на целевой сайт — иначе карточка
        зависит от чужого хостинга. Уже локальные пути (/media/...) не трогаем."""
        if not info.builder_logo_src or info.builder_logo_src.startswith("/"):
            return
        import requests

        try:
            response = requests.get(info.builder_logo_src, timeout=12)
            response.raise_for_status()
        except requests.RequestException:
            return   # логотип не критичен — шаблон уйдёт в текстовый fallback
        filename = logo_filename(self.company.id)
        info.builder_logo_src = self.site_client.upload_file(
            response.content, filename, SERVICE_IMG_DIR)
        self.db.commit()

    def _info_dict(self, info: CompanyInfo) -> dict:
        return {f: getattr(info, f) for f in YANDEX_INFO_FIELDS + AI_TEXT_FIELDS}

    def _create_page(self, info: CompanyInfo, html: str) -> dict:
        name = info.builder_name or self.company.name
        city = info.city_name or self.company.region
        slug = slug_for_company(name, city)
        page = self.site_client.create_page(
            title=f"{name} — {self.company.category_normalized} в {city}",
            url=f"/s/{slug}/", html=html, parent_id=self.site.builder_parent_id,
            meta_description=f"{name} — {self.company.category_normalized} в {city}. "
                             f"Контакты, услуги, отзывы.",
        )
        self.company.remote_page_id = page["id"]
        self.company.remote_url = f"{self.site.base_url}{page.get('url', '')}"
        self.db.commit()
        return page

    def _create_teaser(self, info: CompanyInfo, page: dict) -> None:
        contacts = info.contacts or [{}]
        contact = contacts[0] if contacts else {}
        batch = self.company.batch
        teaser_id = self.site_client.create_teaser(
            name=info.builder_name or self.company.name,
            slug=page.get("url", "").removeprefix("/s/").rstrip("/"),
            address=contact.get("address", "") or info.address,
            phone=contact.get("phone_tel", ""), email=contact.get("email", ""),
            website=self.company.website, page_url=page.get("url", ""),
            category=batch.teaser_category_id, city=batch.teaser_city_id,
            location=batch.teaser_location_id,
        )
        self.company.teaser_id = teaser_id
        self.db.commit()

    def _record_usage(self, tokens_prompt: int, tokens_completion: int, cost: float) -> None:
        if self.job_run_id is None:
            return
        self.db.add(LlmUsage(job_run_id=self.job_run_id, kind="text",
                             model=getattr(self.text_client, "model", ""),
                             tokens_prompt=tokens_prompt,
                             tokens_completion=tokens_completion, cost=cost))
        self.db.commit()


def build_for(db: Session, company: Company, site: Site, site_client,
             job_run_id: int | None) -> None:
    CompanyBuilder(db=db, company=company, site=site,
                   text_client=build_text_client(db), site_client=site_client,
                   job_run_id=job_run_id).build()
```

Проверь сигнатуру `TextResult`/`LLMError` в `app/ai/text.py` перед реализацией
шага 3 — тест использует `TextResult(data=..., tokens_prompt=..., tokens_completion=...,
cost=...)`; если конструктор отличается, подстрой фикстуру теста, а не код билдера.

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_companies_builder.py -v`
Expected: PASS (6 тестов)

- [ ] **Step 5: Commit**

```bash
git add app/companies/builder.py tests/test_companies_builder.py
git commit -m "feat: сборка одной компании — текст, шаблон, страница, тизер"
```

---

## Task 14: Celery-задача запуска партии и повтора компании

**Files:**
- Modify: `execution/backend/app/tasks.py`
- Modify: `execution/backend/app/api/company_batches.py`
- Test: `execution/backend/tests/test_tasks.py`
- Test: `execution/backend/tests/test_api_company_batches.py`

- [ ] **Step 1: Написать падающие тесты для `*_sync`-функций**

```python
# добавить в execution/backend/tests/test_tasks.py
from unittest.mock import Mock, patch

import pytest

from app.models.company import Company, CompanyBatch, CompanyInfo
from app.models.site import Site
from app.tasks import retry_company_sync, run_company_batch_sync


@pytest.fixture
def company_site(db_session):
    site = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               builder_template_html="<div id=\"builder\"></div>", builder_parent_id=10)
    db_session.add(site)
    db_session.commit()
    return site


def test_run_company_batch_marks_done_when_all_published(db_session, company_site):
    batch = CompanyBatch(site_id=company_site.id, region_raw="Самара", category_raw="Дома",
                         category_normalized="Дома под ключ", teaser_category_id=3,
                         teaser_city_id=1, teaser_location_id=1, requested_count=1,
                         status="running")
    db_session.add(batch)
    db_session.commit()
    company = Company(site_id=company_site.id, batch_id=batch.id, site_key="dom.ru",
                      website="https://dom.ru", name="ООО Дом", region="Самара")
    db_session.add(company)
    db_session.commit()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом"))
    db_session.commit()

    with patch("app.tasks.open_site_client", return_value=Mock()), \
         patch("app.tasks.build_for_company") as build_mock:
        def _mark_published(db, c, site, client, job_id):
            c.status = "published"
        build_mock.side_effect = _mark_published
        run_company_batch_sync(db_session, batch.id)

    db_session.refresh(batch)
    assert batch.status == "done"


def test_run_company_batch_skips_already_published(db_session, company_site):
    batch = CompanyBatch(site_id=company_site.id, region_raw="Самара", category_raw="Дома",
                         category_normalized="Дома под ключ", teaser_category_id=3,
                         teaser_city_id=1, teaser_location_id=1, requested_count=1,
                         status="running")
    db_session.add(batch)
    db_session.commit()
    company = Company(site_id=company_site.id, batch_id=batch.id, site_key="dom.ru",
                      website="https://dom.ru", name="ООО Дом", region="Самара",
                      status="published")
    db_session.add(company)
    db_session.commit()

    with patch("app.tasks.open_site_client", return_value=Mock()), \
         patch("app.tasks.build_for_company") as build_mock:
        run_company_batch_sync(db_session, batch.id)

    build_mock.assert_not_called()


def test_retry_company_sync_rebuilds_single_company(db_session, company_site):
    company = Company(site_id=company_site.id, site_key="dom.ru", website="https://dom.ru",
                      name="ООО Дом", region="Самара", status="failed",
                      error_text="старая ошибка")
    db_session.add(company)
    db_session.commit()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом"))
    db_session.commit()

    with patch("app.tasks.open_site_client", return_value=Mock()), \
         patch("app.tasks.build_for_company") as build_mock:
        def _mark_published(db, c, site, client, job_id):
            c.status = "published"
            c.error_text = ""
        build_mock.side_effect = _mark_published
        retry_company_sync(db_session, company.id)

    db_session.refresh(company)
    assert company.status == "published"
    assert company.error_text == ""
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_tasks.py -k company -v`
Expected: FAIL — `ImportError: cannot import name 'run_company_batch_sync'`

- [ ] **Step 3: Дописать `app/tasks.py`**

```python
# execution/backend/app/tasks.py — добавить импорты в начало файла
from app.companies.builder import build_for as build_for_company
from app.models.company import Company, CompanyBatch
```

```python
# execution/backend/app/tasks.py — добавить в конец файла

# --- строители: сборка партии ---

def run_company_batch_sync(db, batch_id: int) -> None:
    batch = db.get(CompanyBatch, batch_id)
    site = db.get(Site, batch.site_id) if batch.site_id is not None else None
    if site is None:
        batch.status = "failed"
        batch.error_text = "сайт этой партии удалён — сборка компаний невозможна"
        db.commit()
        job = _start_job(db, "run_company_batch", None, batch.created_by_id,
                         {"batch_id": batch_id, "companies": len(batch.companies)})
        _finish_job(db, job, "failed", batch.error_text)
        return

    job = _start_job(db, "run_company_batch", site.id, batch.created_by_id,
                     {"batch_id": batch_id, "companies": len(batch.companies)})
    try:
        site_client = open_site_client(db, site)
        for company in batch.companies:
            if company.status == "published":
                continue
            build_for_company(db, company, site, site_client, job.id)
            db.commit()
    except SoftTimeLimitExceeded:
        done = len([c for c in batch.companies if c.status == "published"])
        batch.status = "failed"
        batch.error_text = (f"превышен лимит времени партии, готово "
                            f"{done}/{len(batch.companies)}")
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        done = len([c for c in batch.companies if c.status == "published"])
        batch.status = "failed"
        batch.error_text = f"{exc}; готово {done}/{len(batch.companies)}"
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        return

    batch.status = "done"
    db.commit()
    failed = [c for c in batch.companies if c.status == "failed"]
    _finish_job(db, job, "ok" if not failed else "failed",
               f"готово {len(batch.companies) - len(failed)}/{len(batch.companies)}")


@celery_app.task(name="app.tasks.run_company_batch")
def run_company_batch(batch_id: int) -> None:
    db = SessionLocal()
    try:
        run_company_batch_sync(db, batch_id)
    finally:
        db.close()


# --- строители: повтор одной компании ---

def retry_company_sync(db, company_id: int) -> None:
    company = db.get(Company, company_id)
    site = db.get(Site, company.site_id) if company.site_id is not None else None
    if site is None:
        company.status = "failed"
        company.error_text = "сайт этой компании удалён — повтор невозможен"
        db.commit()
        job = _start_job(db, "retry_company", None, None, {"company_id": company_id})
        _finish_job(db, job, "failed", company.error_text)
        return

    job = _start_job(db, "retry_company", site.id, None, {"company_id": company_id})
    try:
        build_for_company(db, company, site, open_site_client(db, site), job.id)
    except SoftTimeLimitExceeded:
        company.status = "failed"
        company.error_text = "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", company.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        company.status = "failed"
        company.error_text = str(exc)
        db.commit()
        _finish_job(db, job, "failed", str(exc))
        return

    db.commit()
    _finish_job(db, job, "ok" if company.status == "published" else "failed",
               company.error_text)


@celery_app.task(name="app.tasks.retry_company")
def retry_company(company_id: int) -> None:
    db = SessionLocal()
    try:
        retry_company_sync(db, company_id)
    finally:
        db.close()
```

- [ ] **Step 4: Запустить тесты `*_sync` и убедиться, что они проходят**

Run: `pytest tests/test_tasks.py -k company -v`
Expected: PASS (3 теста)

- [ ] **Step 5: Дописать падающие тесты API `/run` и `/retry`**

```python
# добавить в execution/backend/tests/test_api_company_batches.py
@pytest.fixture
def no_celery(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.api.company_batches.run_company_batch.apply_async",
        lambda args, **kwargs: sent.append(("run", args[0])) or type("R", (), {"id": "t"})())
    monkeypatch.setattr(
        "app.api.company_batches.retry_company.apply_async",
        lambda args, **kwargs: sent.append(("retry", args[0])) or type("R", (), {"id": "t"})())
    return sent


def test_run_dispatches_task_and_marks_running(manager_client, site_id, candidates, no_celery):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    resp = manager_client.post(f"/api/company-batches/{batch['id']}/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert no_celery == [("run", batch["id"])]


def test_run_twice_dispatches_once(manager_client, site_id, candidates, no_celery):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    manager_client.post(f"/api/company-batches/{batch['id']}/run")
    resp = manager_client.post(f"/api/company-batches/{batch['id']}/run")
    assert resp.status_code == 400
    assert len(no_celery) == 1


def test_run_rejects_empty_batch(manager_client, site_id):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    resp = manager_client.post(f"/api/company-batches/{batch['id']}/run")
    assert resp.status_code == 400
```

- [ ] **Step 6: Запустить и убедиться, что падают**

Run: `pytest tests/test_api_company_batches.py -k run -v`
Expected: FAIL — `AttributeError` (эндпоинта `/run` ещё нет)

- [ ] **Step 7: Добавить эндпоинт `/run` в `app/api/company_batches.py`**

```python
# execution/backend/app/api/company_batches.py — добавить импорт
from app.tasks import retry_company, run_company_batch
```

```python
# execution/backend/app/api/company_batches.py — добавить в конец файла
@router.post("/company-batches/{batch_id}/run", response_model=BatchOut)
def run(batch_id: int, db: Session = Depends(get_db),
       _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if not batch.companies:
        raise HTTPException(400, "в партии нет компаний")
    if batch.status == "running":
        raise HTTPException(400, "партия уже выполняется")
    batch.status = "running"
    db.commit()
    run_company_batch.apply_async(args=[batch.id])
    return _to_out(db, batch)


@router.post("/companies/{company_id}/retry")
def retry(company_id: int, db: Session = Depends(get_db),
         _user: User = Depends(get_current_user)):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(404, "компания не найдена")
    if company.status in ("published", "generating"):
        detail = ("компания уже опубликована" if company.status == "published"
                  else "компания уже собирается — повторный запуск не требуется")
        raise HTTPException(400, detail)
    company.status = "generating"
    db.commit()
    retry_company.apply_async(args=[company.id])
    return {"ok": True}
```

- [ ] **Step 8: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_api_company_batches.py tests/test_tasks.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add app/tasks.py app/api/company_batches.py tests/test_tasks.py tests/test_api_company_batches.py
git commit -m "feat: запуск партии и повтор компании через Celery"
```

---

## Task 15: Миграция данных из старого CLI

**Files:**
- Create: `execution/backend/migrate_companies_from_cli.py`
- Test: `execution/backend/tests/test_migrate_companies_from_cli.py`

Разовый скрипт: `execution/data/companies.db` (sqlite) → Postgres. Критичен
для дедупа — без него партии предложат уже опубликованные вручную компании
повторно (design doc §6).

- [ ] **Step 1: Написать падающий тест**

```python
# execution/backend/tests/test_migrate_companies_from_cli.py
import sqlite3
from pathlib import Path

import pytest

from app.models.company import Company, CompanyInfo
from app.models.site import Site
from migrate_companies_from_cli import migrate


@pytest.fixture
def cli_db(tmp_path) -> Path:
    path = tmp_path / "companies.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY, region TEXT, sphere TEXT, name TEXT,
            website TEXT UNIQUE, reviews_count INTEGER DEFAULT 0, rating REAL,
            yandex_url TEXT
        );
        CREATE TABLE company_info (
            company_id INTEGER UNIQUE, builder_name TEXT, city_name TEXT,
            city_prepositional TEXT, builder_logo_src TEXT, builder_logo_alt TEXT,
            about_company TEXT, specialization TEXT, projects_services TEXT,
            benefits TEXT, contacts TEXT, address TEXT, coordinates TEXT
        );
        CREATE TABLE generated_content (
            company_id INTEGER, target_site TEXT, html_content TEXT,
            page_url TEXT, published INTEGER DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO companies (id, region, sphere, name, website, "
                "reviews_count, rating) VALUES (1, 'Самара', 'дома', 'ООО Дом', "
                "'https://dom.ru', 12, 4.7)")
    conn.execute("INSERT INTO company_info (company_id, builder_name, city_name) "
                "VALUES (1, 'ООО Дом', 'Самара')")
    conn.execute("INSERT INTO generated_content (company_id, target_site, page_url, "
                "published) VALUES (1, 'https://vetonit-center.ru', '/s/ooo-dom/', 1)")
    conn.commit()
    conn.close()
    return path


def test_migrate_creates_company_scoped_to_site(db_session, cli_db):
    site = Site(name="Ветонит", domain="vetonit-center.ru", base_url="https://vetonit-center.ru",
               api_token_enc="e")
    db_session.add(site)
    db_session.commit()

    report = migrate(db_session, cli_db)

    assert report.migrated == 1
    assert report.unmatched_sites == []
    company = db_session.query(Company).one()
    assert company.site_id == site.id
    assert company.site_key == "dom.ru"
    assert company.status == "published"
    assert company.info.builder_name == "ООО Дом"


def test_migrate_reports_unmatched_target_site(db_session, cli_db):
    report = migrate(db_session, cli_db)   # ни один Site не заведён
    assert report.migrated == 0
    assert report.unmatched_sites == ["vetonit-center.ru"]


def test_migrate_is_idempotent(db_session, cli_db):
    site = Site(name="Ветонит", domain="vetonit-center.ru", base_url="https://vetonit-center.ru",
               api_token_enc="e")
    db_session.add(site)
    db_session.commit()

    migrate(db_session, cli_db)
    second = migrate(db_session, cli_db)

    assert second.migrated == 0   # уже перенесено — не задваиваем
    assert db_session.query(Company).count() == 1
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_migrate_companies_from_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'migrate_companies_from_cli'`

- [ ] **Step 3: Реализовать скрипт**

```python
# execution/backend/migrate_companies_from_cli.py
"""Разовая миграция execution/data/companies.db (CLI, SQLite) → Postgres.

Запуск: python migrate_companies_from_cli.py [--file путь/к/companies.db]

Критично выполнить до включения раздела «Строители» в проде: без неё дедуп
по (site_id, site_key) не увидит уже опубликованные вручную компании — см.
directions/2026-08-06-builders-import-design.md §6.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.import_xlsx import site_key as normalize_site_key
from app.db import SessionLocal
from app.models.company import Company, CompanyInfo
from app.models.site import Site

DEFAULT_CLI_DB = Path(__file__).parent.parent / "data" / "companies.db"


@dataclass
class MigrationReport:
    migrated: int = 0
    unmatched_sites: list[str] = field(default_factory=list)


def _site_domain_from_target(target_site: str) -> str:
    return urlparse(target_site).netloc.lower().removeprefix("www.")


def migrate(db: Session, cli_db_path: Path) -> MigrationReport:
    report = MigrationReport()
    conn = sqlite3.connect(cli_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT c.id, c.region, c.name, c.website, c.reviews_count, c.rating,
                   c.yandex_url, gc.target_site, gc.page_url, gc.published,
                   ci.builder_name, ci.city_name, ci.city_prepositional,
                   ci.builder_logo_src, ci.builder_logo_alt, ci.about_company,
                   ci.specialization, ci.projects_services, ci.benefits,
                   ci.contacts, ci.address, ci.coordinates
            FROM companies c
            JOIN generated_content gc ON gc.company_id = c.id
            LEFT JOIN company_info ci ON ci.company_id = c.id
        """).fetchall()

        unmatched: set[str] = set()
        for row in rows:
            domain = _site_domain_from_target(row["target_site"])
            site = db.scalars(select(Site).where(Site.domain == domain)).first()
            if site is None:
                unmatched.add(domain)
                continue

            key = normalize_site_key(row["website"])
            existing = db.scalars(
                select(Company).where(Company.site_id == site.id, Company.site_key == key)
            ).first()
            if existing is not None:
                continue   # уже перенесено — идемпотентность

            company = Company(
                site_id=site.id, site_key=key, website=row["website"], name=row["name"],
                region=row["region"] or "", rating=row["rating"],
                reviews_count=row["reviews_count"] or 0, yandex_url=row["yandex_url"] or "",
                status="published" if row["page_url"] else "failed",
                remote_url=row["page_url"] or "",
                error_text="" if row["page_url"] else "перенесено из CLI без готовой страницы",
            )
            db.add(company)
            db.flush()

            contacts = row["contacts"]
            try:
                contacts = json.loads(contacts) if contacts else []
            except (TypeError, json.JSONDecodeError):
                contacts = []

            db.add(CompanyInfo(
                company_id=company.id, builder_name=row["builder_name"] or "",
                city_name=row["city_name"] or "", city_prepositional=row["city_prepositional"] or "",
                builder_logo_src=row["builder_logo_src"] or "",
                builder_logo_alt=row["builder_logo_alt"] or "",
                about_company=row["about_company"] or "", specialization=row["specialization"] or "",
                projects_services=row["projects_services"] or "", benefits=row["benefits"] or "",
                contacts=contacts, address=row["address"] or "", coordinates=row["coordinates"] or "",
            ))
            report.migrated += 1

        report.unmatched_sites = sorted(unmatched)
    finally:
        conn.close()

    db.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Миграция companies.db → Postgres")
    parser.add_argument("--file", default=str(DEFAULT_CLI_DB))
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Файл не найден: {path}")

    db = SessionLocal()
    try:
        report = migrate(db, path)
    finally:
        db.close()

    print(f"Перенесено компаний: {report.migrated}")
    if report.unmatched_sites:
        print(f"Не найден Site для доменов (заведи их в /admin/sites и запусти повторно): "
             f"{', '.join(report.unmatched_sites)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_migrate_companies_from_cli.py -v`
Expected: PASS (3 теста)

- [ ] **Step 5: Commit**

```bash
git add migrate_companies_from_cli.py tests/test_migrate_companies_from_cli.py
git commit -m "feat: разовая миграция companies.db из CLI в Postgres"
```

---

## Task 16: Frontend — API-клиент

**Files:**
- Modify: `execution/frontend/src/api.ts`

- [ ] **Step 1: Добавить типы и функции**

```typescript
// execution/frontend/src/api.ts — добавить рядом с существующими интерфейсами
export interface Facets { regions: string[]; categories: string[] }
export interface CompanyImportResult {
  id: number; filename: string; row_count: number; matched_count: number
  error_count: number; status: string; error_message: string
}
export interface CompanyRow {
  id: number; name: string; website: string; region: string
  rating: number | null; reviews_count: number; status: string
  remote_url: string; error_text: string
}
export interface CompanyBatchRow {
  id: number; site_id: number; site_name: string
  region_raw: string; category_raw: string; category_normalized: string
  requested_count: number; status: string; error_text: string
  created_at: string; companies: CompanyRow[]
}
```

```typescript
// execution/frontend/src/api.ts — добавить рядом с export const getBatches и др.
export const uploadCompanyImport = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return api.post<CompanyImportResult>('/company-imports', form).then(r => r.data)
}
export const getCompanyFacets = (siteId: number) =>
  api.get<Facets>(`/company-imports/facets?site_id=${siteId}`).then(r => r.data)

export const getCompanyBatches = () =>
  api.get<CompanyBatchRow[]>('/company-batches').then(r => r.data)
export const getCompanyBatch = (id: number) =>
  api.get<CompanyBatchRow>(`/company-batches/${id}`).then(r => r.data)
export const createCompanyBatch = (d: {
  site_id: number; region_raw: string; category_raw: string
  category_normalized: string; teaser_category_id: number
  teaser_city_id: number; teaser_location_id: number; count: number
}) => api.post<CompanyBatchRow>('/company-batches', d).then(r => r.data)
export const removeBatchCompany = (batchId: number, companyId: number) =>
  api.delete<CompanyBatchRow>(`/company-batches/${batchId}/companies/${companyId}`)
    .then(r => r.data)
export const addNextBatchCompany = (batchId: number) =>
  api.post<CompanyBatchRow>(`/company-batches/${batchId}/companies/next`).then(r => r.data)
export const runCompanyBatch = (id: number) =>
  api.post<CompanyBatchRow>(`/company-batches/${id}/run`).then(r => r.data)
export const retryCompany = (id: number) => api.post(`/companies/${id}/retry`)
```

- [ ] **Step 2: Проверить типы**

Run: `cd execution/frontend && npx tsc --noEmit`
Expected: без ошибок

- [ ] **Step 3: Commit**

```bash
git add src/api.ts
git commit -m "feat: API-клиент раздела «Строители»"
```

---

## Task 17: Frontend — список партий и загрузка xlsx

**Files:**
- Create: `execution/frontend/src/pages/BuildersPage.tsx`

- [ ] **Step 1: Реализовать страницу**

По образцу `ArticlesPage.tsx`: таблица партий + модалка загрузки xlsx +
модалка создания партии (site → facets по сайту → region/category select →
category_normalized + 3 teaser id + count).

```typescript
// execution/frontend/src/pages/BuildersPage.tsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Button, Card, Form, InputNumber, Modal, Select, Space, Table, Tag, Typography,
  Upload, message,
} from 'antd'
import { PlusOutlined, UploadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { UploadProps } from 'antd'
import {
  CompanyBatchRow, Facets, SiteBrief, createCompanyBatch, getCompanyBatches,
  getCompanyFacets, getSites, uploadCompanyImport,
} from '../api'

const STATUS: Record<string, { color: string; label: string }> = {
  selection_review: { color: 'warning', label: 'Список на согласовании' },
  running: { color: 'processing', label: 'Генерируется' },
  done: { color: 'success', label: 'Готово' },
  failed: { color: 'error', label: 'Ошибка' },
}

export default function BuildersPage() {
  const navigate = useNavigate()
  const [batches, setBatches] = useState<CompanyBatchRow[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [open, setOpen] = useState(false)
  const [facets, setFacets] = useState<Facets>({ regions: [], categories: [] })
  const [form] = Form.useForm()

  const load = () => getCompanyBatches().then(setBatches)

  useEffect(() => {
    load()
    getSites().then(setSites)
  }, [])

  useEffect(() => {
    const active = batches.some(b => b.status === 'running')
    if (!active) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [batches])

  const onSiteChange = async (siteId: number) => {
    setFacets(await getCompanyFacets(siteId))
    form.setFieldsValue({ region_raw: undefined, category_raw: undefined })
  }

  const submit = async (values: {
    site_id: number; region_raw: string; category_raw: string
    category_normalized: string; teaser_category_id: number
    teaser_city_id: number; teaser_location_id: number; count: number
  }) => {
    try {
      const batch = await createCompanyBatch(values)
      setOpen(false)
      form.resetFields()
      navigate(`/builders/${batch.id}`)
    } catch {
      // сообщение об ошибке уже показывает интерцептор api.ts
    }
  }

  const uploadProps: UploadProps = {
    accept: '.xlsx',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      try {
        const result = await uploadCompanyImport(file as File)
        if (result.status === 'failed') {
          message.error(result.error_message || 'Не удалось разобрать файл')
        } else {
          message.success(
            `Загружено: ${result.matched_count} компаний из ${result.row_count} строк`)
        }
        onSuccess?.(result)
      } catch (e) {
        onError?.(e as Error)
      }
    },
  }

  return (
    <>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Партии строителей</Typography.Title>
        <Space>
          <Upload {...uploadProps}>
            <Button icon={<UploadOutlined />}>Загрузить выгрузку Яндекс.Карт</Button>
          </Upload>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            Новая партия
          </Button>
        </Space>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id"
          dataSource={batches}
          pagination={{ pageSize: 20 }}
          onRow={r => ({ onClick: () => navigate(`/builders/${r.id}`),
                         style: { cursor: 'pointer' } })}
          columns={[
            { title: 'Сайт', dataIndex: 'site_name' },
            { title: 'Регион', dataIndex: 'region_raw' },
            { title: 'Категория', dataIndex: 'category_raw' },
            { title: 'Компаний', dataIndex: 'requested_count', width: 100 },
            {
              title: 'Готово', width: 110,
              render: (_, r: CompanyBatchRow) =>
                `${r.companies.filter(c => c.status === 'published').length} / ${r.companies.length}`,
            },
            {
              title: 'Статус', dataIndex: 'status', width: 200,
              render: (s: string) => <Tag color={STATUS[s]?.color}>{STATUS[s]?.label ?? s}</Tag>,
            },
            {
              title: 'Создана', dataIndex: 'created_at', width: 160,
              render: (v: string) => dayjs(v).format('DD.MM.YYYY HH:mm'),
            },
          ]}
        />
      </Card>

      <Modal open={open} onCancel={() => setOpen(false)} onOk={form.submit}
             title="Новая партия строителей" okText="Отобрать компании" destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={submit}
              initialValues={{ count: 10 }} requiredMark={false}>
          <Form.Item name="site_id" label="Сайт"
                     rules={[{ required: true, message: 'Выберите сайт' }]}>
            <Select placeholder="Выберите сайт" onChange={onSiteChange}
                    options={sites.map(s => ({ value: s.id, label: `${s.name} — ${s.domain}` }))} />
          </Form.Item>
          <Form.Item name="region_raw" label="Регион (из выгрузки)"
                     rules={[{ required: true, message: 'Выберите регион' }]}>
            <Select placeholder="Регион" options={facets.regions.map(r => ({ value: r, label: r }))} />
          </Form.Item>
          <Form.Item name="category_raw" label="Категория (из выгрузки)"
                     rules={[{ required: true, message: 'Выберите категорию' }]}>
            <Select placeholder="Категория"
                    options={facets.categories.map(c => ({ value: c, label: c }))} />
          </Form.Item>
          <Form.Item name="category_normalized" label="Название сферы для этого сайта"
                     rules={[{ required: true, message: 'Укажите нормализованное имя' }]}>
            <Select mode="tags" maxCount={1} placeholder="Например: Дома под ключ" />
          </Form.Item>
          <Space.Compact block>
            <Form.Item name="teaser_category_id" label="Category ID" style={{ width: '33%' }}
                       rules={[{ required: true, message: 'ID' }]}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="teaser_city_id" label="City ID" style={{ width: '33%' }}
                       rules={[{ required: true, message: 'ID' }]}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="teaser_location_id" label="Location ID" style={{ width: '34%' }}
                       rules={[{ required: true, message: 'ID' }]}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="count" label="Сколько компаний"
                     rules={[{ required: true, message: 'Укажите количество' }]}>
            <InputNumber min={1} max={50} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
```

`Select mode="tags" maxCount={1}` для `category_normalized` даёт свободный
ввод одной строки через существующий компонент antd без кастомного `Input`
внутри `Select`-подобного layout'а — оператор печатает значение и жмёт Enter.

- [ ] **Step 2: Проверить типы**

Run: `cd execution/frontend && npx tsc --noEmit`
Expected: без ошибок

- [ ] **Step 3: Commit**

```bash
git add src/pages/BuildersPage.tsx
git commit -m "feat: страница партий строителей — список, загрузка xlsx, создание"
```

---

## Task 18: Frontend — превью партии и запуск

**Files:**
- Create: `execution/frontend/src/pages/BuilderBatchPage.tsx`

- [ ] **Step 1: Реализовать страницу**

По образцу `BatchPage.tsx`: пока `status === 'selection_review'` — таблица
кандидатов с кнопками «Убрать» и «Добрать ещё» плюс «Запустить генерацию»;
после запуска — таблица статусов с повтором по упавшим.

```typescript
// execution/frontend/src/pages/BuilderBatchPage.tsx
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Alert, Button, Card, Popconfirm, Space, Table, Tag, Typography, message } from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  CompanyBatchRow, CompanyRow, addNextBatchCompany, getCompanyBatch, removeBatchCompany,
  retryCompany, runCompanyBatch,
} from '../api'

const COMPANY_STATUS: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: 'Ожидает' },
  generating: { color: 'processing', label: 'Собирается' },
  published: { color: 'success', label: 'Опубликован черновик' },
  failed: { color: 'error', label: 'Ошибка' },
}

export default function BuilderBatchPage() {
  const { id } = useParams()
  const batchId = Number(id)
  const [batch, setBatch] = useState<CompanyBatchRow | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => getCompanyBatch(batchId).then(setBatch)

  useEffect(() => { load() }, [batchId])

  useEffect(() => {
    if (!batch) return
    const active = batch.status === 'running'
      || batch.companies.some(c => c.status === 'generating')
    if (!active) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [batch])

  if (!batch) return null

  const removeCompany = async (companyId: number) => {
    setBusy(true)
    try { setBatch(await removeBatchCompany(batchId, companyId)) } finally { setBusy(false) }
  }

  const addNext = async () => {
    setBusy(true)
    try { setBatch(await addNextBatchCompany(batchId)) }
    catch { /* сообщение уже показал интерцептор */ }
    finally { setBusy(false) }
  }

  const run = async () => {
    setBusy(true)
    try {
      setBatch(await runCompanyBatch(batchId))
      message.success('Запущено — сборка страниц пойдёт в фоне')
    } finally { setBusy(false) }
  }

  const editable = batch.status === 'selection_review'

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        Партия №{batch.id} — {batch.site_name}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        {batch.region_raw} · {batch.category_raw} → «{batch.category_normalized}»
      </Typography.Paragraph>

      {batch.error_text && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }}
               message="Партия завершилась с ошибкой" description={batch.error_text} />
      )}

      {editable ? (
        <Card title="Отобранные компании" extra={
          <Space>
            <Button icon={<PlusOutlined />} disabled={busy} onClick={addNext}>
              Добрать ещё
            </Button>
            <Button type="primary" loading={busy}
                    disabled={busy || batch.companies.length === 0} onClick={run}>
              Запустить генерацию
            </Button>
          </Space>
        }>
          <Table
            rowKey="id"
            dataSource={batch.companies}
            pagination={false}
            columns={[
              { title: 'Компания', dataIndex: 'name' },
              { title: 'Сайт', dataIndex: 'website' },
              { title: 'Отзывов', dataIndex: 'reviews_count', width: 100 },
              { title: 'Рейтинг', dataIndex: 'rating', width: 100 },
              {
                title: '', width: 60,
                render: (_, r: CompanyRow) => (
                  <Popconfirm title="Убрать компанию из партии?"
                              onConfirm={() => removeCompany(r.id)}>
                    <Button type="text" icon={<DeleteOutlined />} disabled={busy} />
                  </Popconfirm>
                ),
              },
            ]}
          />
        </Card>
      ) : (
        <Card styles={{ body: { padding: 0 } }}>
          <Table
            rowKey="id"
            dataSource={batch.companies}
            pagination={false}
            columns={[
              { title: 'Компания', dataIndex: 'name' },
              {
                title: 'Статус', dataIndex: 'status', width: 220,
                render: (s: string) => (
                  <Tag color={COMPANY_STATUS[s]?.color}>{COMPANY_STATUS[s]?.label ?? s}</Tag>
                ),
              },
              {
                title: 'Черновик', width: 140,
                render: (_, r: CompanyRow) => r.remote_url
                  ? <a href={r.remote_url} target="_blank" rel="noreferrer">открыть</a>
                  : '—',
              },
              {
                title: '', width: 60,
                render: (_, r: CompanyRow) => r.status === 'failed' ? (
                  <Popconfirm title="Повторить сборку этой компании?"
                              onConfirm={async () => { await retryCompany(r.id); load() }}>
                    <Button type="text" icon={<ReloadOutlined />} />
                  </Popconfirm>
                ) : null,
              },
            ]}
            expandable={{
              expandedRowRender: (r: CompanyRow) => (
                <Typography.Text type="danger">{r.error_text}</Typography.Text>
              ),
              rowExpandable: (r: CompanyRow) => Boolean(r.error_text),
            }}
          />
        </Card>
      )}
    </>
  )
}
```

- [ ] **Step 2: Проверить типы**

Run: `cd execution/frontend && npx tsc --noEmit`
Expected: без ошибок

- [ ] **Step 3: Commit**

```bash
git add src/pages/BuilderBatchPage.tsx
git commit -m "feat: страница превью и запуска партии строителей"
```

---

## Task 19: Подключить маршруты

**Files:**
- Modify: `execution/frontend/src/App.tsx`

- [ ] **Step 1: Заменить заглушку `/builders` на реальные страницы**

```typescript
// execution/frontend/src/App.tsx — добавить импорты
import BuildersPage from './pages/BuildersPage'
import BuilderBatchPage from './pages/BuilderBatchPage'
```

```typescript
// execution/frontend/src/App.tsx — заменить блок
            <Route path="/builders" element={
              <div style={{ color: '#71717a' }}>
                Раздел «Строители» появится в плане 2. Пока процесс идёт через CLI.
              </div>
            } />
// на
            <Route path="/builders" element={<BuildersPage />} />
            <Route path="/builders/:id" element={<BuilderBatchPage />} />
```

- [ ] **Step 2: Проверить сборку**

Run: `cd execution/frontend && npx tsc --noEmit && npm run build`
Expected: без ошибок, `dist/` собран

- [ ] **Step 3: Commit**

```bash
git add src/App.tsx
git commit -m "feat: подключить раздел «Строители» в навигацию"
```

---

## Task 20: Полный прогон и ручная проверка

**Files:** нет новых файлов — проверочный шаг.

- [ ] **Step 1: Полный прогон backend-тестов**

Run: `cd execution/backend && pytest -v`
Expected: все тесты проходят, включая существующие (регрессия по разделу «Статьи»)

- [ ] **Step 2: Прогон с покрытием**

Run: `pytest --cov=app --cov-report=term-missing`
Expected: новые модули (`app/companies/*`) покрыты; отсутствие покрытия
только в тривиальных ветках (`if __name__ == "__main__"` и т.п.)

- [ ] **Step 3: Применить миграцию и seed на чистой БД (docker compose)**

Run:
```bash
docker compose down -v
docker compose up -d postgres redis
docker compose run --rm backend alembic upgrade head
docker compose up -d backend worker frontend
```
Expected: контейнеры стартуют без ошибок, `GET /api/health` отвечает `{"status": "ok"}`

- [ ] **Step 4: Ручная проверка в браузере**

1. Завести тестовый `Site` с `builder_template_html` и `builder_parent_id`
   (через `/admin/sites`).
2. Загрузить тестовый xlsx (можно `builders_yandex_2026-07-10.xlsx` из корня
   репозитория) на `/builders`.
3. Создать партию, убедиться, что список компаний отсортирован по отзывам
   и что facets показывают реальные значения из файла.
4. Вычеркнуть одну компанию, нажать «Добрать ещё» — убедиться, что список
   восстановился до нужного количества.
5. Запустить партию (если RouterAI-ключ и `builder_template_html` не
   настроены — ожидаемо получить `failed` с понятным текстом ошибки,
   это тоже валидная проверка обработки отказа).

- [ ] **Step 5: Запустить разовую миграцию на копии продовых данных (если есть доступ)**

Run: `docker compose run --rm backend python migrate_companies_from_cli.py --file /path/to/companies.db`
Expected: вывод со числом перенесённых компаний и списком доменов без
сопоставленного `Site`, если такие есть — завести их в `/admin/sites` и
перезапустить.

---

## Порядок выполнения

Задачи строго последовательны 1→20: каждая следующая опирается на модели/
модули предыдущей. Backend (1-15) можно вести обычным TDD-циклом одного
разработчика; frontend (16-19) требует поднятого backend для проверки
типов ответов API, но пишется независимо от бизнес-логики генерации.
