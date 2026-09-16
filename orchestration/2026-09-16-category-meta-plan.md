# Метатеги категорий по Wordstat — план реализации

> **Для исполнителей-агентов:** ОБЯЗАТЕЛЬНЫЙ ПОДСКИЛЛ: superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans. Шаги отмечаются чекбоксами (`- [ ]`).

**Цель:** раздел панели «Метатеги категорий» — по статистике Яндекс Wordstat пишет title, h1, description, keywords и ai_keywords для категорий каталога и записывает их на сайт через `/api/v1/metatags/`.

**Архитектура:** факты и решения — кодом (дерево категорий из API сайта, частоты и выбор словоформы из Wordstat, проверка тегов), тексты — LLM (RouterAI) по двум редактируемым промптам. Категории обрабатываются цепочкой Celery-задач — не больше одного слота воркера на проект; глобальный ограничитель держит квоту Wordstat 100 запросов в час, ответы кешируются на 30 дней.

**Стек:** FastAPI, SQLAlchemy 2, Alembic, Celery + Redis, requests; React 18 + antd 5.

**Дизайн:** `directions/2026-09-16-category-meta-design.md` — читать перед началом.

**Происхождение кода:** весь код и все тесты этого плана прогнаны до записи плана на копии бэкенда (778 тестов зелёные, `alembic check` без расхождений на Postgres 16, `npm run build` проходит). Копируйте блоки дословно; если тест не проходит — это дефект реализации или среды, а не повод править тест. Если всё же нашли дефект в самом плане — правка идёт в ДВА места: код и текст плана.

---

## Структура файлов

```
execution/backend/
  app/clock.py                                  Modify: as_utc()
  app/models/site.py                            Modify: meta_enabled, city, city_in, wordstat_region_id, brand
  app/models/category_meta.py                   Create: CategoryMeta, MetaRun, WordstatCache, WordstatCall
  app/models/__init__.py                        Modify: регистрация моделей
  alembic/versions/c7d2e9a41b30_category_meta.py Create
  app/seed.py                                   Modify: настройки Wordstat, промпты category_seeds/category_meta
  app/ai/prompts.py                             Modify: PROMPT_KEYS, PROMPT_VARIABLES
  app/wordstat/__init__.py                      Create (пустой)
  app/wordstat/client.py                        Create: HTTP-клиент Wordstat, parse_top
  app/wordstat/quota.py                         Create: глобальный ограничитель, QuotaExceeded
  app/wordstat/cache.py                         Create: кеш ответов на 30 дней
  app/wordstat/regions.py                       Create: дерево регионов, поиск по городу
  app/wordstat/factory.py                       Create: клиент и настройки из БД
  app/sites/client.py                           Modify: категории, метатеги, sitemap
  app/category_meta/__init__.py                 Create (пустой)
  app/category_meta/forms.py                    Create: выбор словоформы
  app/category_meta/validate.py                 Create: проверка тегов
  app/category_meta/tree.py                     Create: синхронизация дерева категорий
  app/category_meta/seeds.py                    Create: поисковые фразы через LLM
  app/category_meta/city.py                     Create: «в Москве» через LLM
  app/category_meta/publish.py                  Create: запись метатега на сайт
  app/category_meta/generator.py                Create: одна категория целиком
  app/category_meta/runs.py                     Create: прогресс, зависшие, оборванная цепочка
  app/tasks.py                                  Modify: start_meta_run, generate_category_meta
  app/api/category_meta.py                      Create: API раздела
  app/main.py                                   Modify: подключение роутера
  tests/…                                       Create/Modify: по задачам

execution/frontend/src/
  api.ts                                        Modify: типы и вызовы
  statuses.ts                                   Modify: CATEGORY_META_STATUS
  App.tsx                                       Modify: пункт меню и маршрут
  pages/CategoryMetaPage.tsx                    Create: проекты
  pages/CategoryMetaTree.tsx                    Create: дерево категорий и карточка
  pages/AdminSettingsPage.tsx                   Modify: поля Wordstat
  pages/AdminPromptsPage.tsx                    Modify: два новых промпта
  changelog.ts                                  Modify: запись в «Доработки»
```

## Как запускать

Все команды — из `execution/`.

```bash
docker compose run --rm --no-deps backend pytest -q tests/test_x.py   # тесты на SQLite в памяти
docker compose run --rm --no-deps backend pytest -q                   # полный регресс (~2 мин)
docker compose run --rm frontend sh -c "npm ci && npm run build"      # сборка фронта
```

Локальный `execution/frontend/node_modules` неполный (нет `react-router-dom`) — собирать только в контейнере после `npm ci`.

---

### Task 1: Модели, миграция, `as_utc`

**Files:**
- Modify: `execution/backend/app/clock.py`
- Modify: `execution/backend/app/models/site.py`
- Create: `execution/backend/app/models/category_meta.py`
- Modify: `execution/backend/app/models/__init__.py`
- Create: `execution/backend/alembic/versions/c7d2e9a41b30_category_meta.py`
- Test: `execution/backend/tests/test_models_category_meta.py`, `execution/backend/tests/test_clock_as_utc.py`

- [ ] **Step 1: Тесты (красные)**

`tests/test_clock_as_utc.py`:

```python
from datetime import datetime, timedelta, timezone

from app.clock import as_utc


def test_as_utc_marks_naive_as_utc():
    assert as_utc(datetime(2026, 9, 16, 12, 0)) == datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def test_as_utc_keeps_aware():
    moment = datetime(2026, 9, 16, 15, 0, tzinfo=timezone(timedelta(hours=3)))
    assert as_utc(moment) is moment
```

`tests/test_models_category_meta.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.category_meta import CategoryMeta, MetaRun, WordstatCache, WordstatCall
from app.models.site import Site


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


def test_site_meta_fields_default_empty(site):
    assert site.meta_enabled is False
    assert (site.city, site.city_in, site.brand) == ("", "", "")
    assert site.wordstat_region_id is None


def test_category_defaults(db_session, site):
    row = CategoryMeta(site_id=site.id, remote_id=46, name="Фанера")
    db_session.add(row)
    db_session.commit()
    assert row.status == "new"
    assert row.previous_json is None
    assert row.low_demand is False
    assert row.updated_at is not None


def test_category_unique_per_site(db_session, site):
    db_session.add_all([CategoryMeta(site_id=site.id, remote_id=46, name="А"),
                        CategoryMeta(site_id=site.id, remote_id=46, name="Б")])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_previous_json_roundtrip(db_session, site):
    row = CategoryMeta(site_id=site.id, remote_id=1, name="А",
                       previous_json={"title": "Фанера", "h1": ""})
    db_session.add(row)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(CategoryMeta, row.id).previous_json == {"title": "Фанера", "h1": ""}


def test_run_and_wordstat_rows(db_session, site):
    run = MetaRun(site_id=site.id)
    db_session.add_all([run, WordstatCache(kind="top", phrase="фанера", region_id=213,
                                           body={"totalCount": "1"}),
                        WordstatCall()])
    db_session.commit()
    assert run.finished_at is None and run.total == 0 and run.error_text == ""


def test_wordstat_cache_key_unique(db_session):
    db_session.add_all([WordstatCache(kind="top", phrase="фанера", region_id=213, body={}),
                        WordstatCache(kind="top", phrase="фанера", region_id=213, body={})])
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [ ] **Step 2: Убедиться, что падают**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_clock_as_utc.py tests/test_models_category_meta.py`
Expected: FAIL — `ImportError: cannot import name 'as_utc'` и `ModuleNotFoundError: No module named 'app.models.category_meta'`.

- [ ] **Step 3: `as_utc` в `app/clock.py`**

Вставить перед `def seconds_since(`:

```python
def as_utc(moment: datetime) -> datetime:
    """Момент из БД — с таймзоной. Postgres отдаёт aware, SQLite (тесты) — naive;
    naive трактуем как UTC, как и в seconds_since ниже."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment
```

- [ ] **Step 4: Поля сайта**

В конец класса `Site` (`app/models/site.py`, после `builder_reference_synced_at`):

```python
    # --- метатеги категорий (directions/2026-09-16-category-meta-design.md) ---
    meta_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    city: Mapped[str] = mapped_column(String(200), default="")
    # С предлогом: «в Москве», «во Владимире» — предлог зависит от слова, поэтому
    # хранится готовой фразой, а не падежной формой.
    city_in: Mapped[str] = mapped_column(String(200), default="")
    wordstat_region_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    brand: Mapped[str] = mapped_column(String(200), default="")
```

- [ ] **Step 5: Модели — `app/models/category_meta.py`**

```python
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
```

- [ ] **Step 6: Регистрация в `app/models/__init__.py`**

Добавить импорт перед `from app.models.company import (`:

```python
from app.models.category_meta import CategoryMeta, MetaRun, WordstatCache, WordstatCall
```

и в `__all__` перед строкой `"Company", "CompanyBatch", ...`:

```python
    "CategoryMeta", "MetaRun", "WordstatCache", "WordstatCall",
```

- [ ] **Step 7: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_clock_as_utc.py tests/test_models_category_meta.py tests/test_models_site.py`
Expected: PASS (18 passed).

- [ ] **Step 8: Миграция**

Текущая голова — `4353fdfa69a2`. Создать `alembic/versions/c7d2e9a41b30_category_meta.py`:

```python
"""category meta

Revision ID: c7d2e9a41b30
Revises: 4353fdfa69a2
Create Date: 2026-09-16 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7d2e9a41b30'
down_revision: Union[str, None] = '4353fdfa69a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    # server_default у NOT NULL-колонок — чтобы миграция прошла на уже заведённых сайтах.
    op.add_column('sites', sa.Column('meta_enabled', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.add_column('sites', sa.Column('city', sa.String(length=200), nullable=False,
                                     server_default=''))
    op.add_column('sites', sa.Column('city_in', sa.String(length=200), nullable=False,
                                     server_default=''))
    op.add_column('sites', sa.Column('wordstat_region_id', sa.Integer(), nullable=True))
    op.add_column('sites', sa.Column('brand', sa.String(length=200), nullable=False,
                                     server_default=''))

    op.create_table('category_meta',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('remote_id', sa.Integer(), nullable=False),
    sa.Column('remote_parent_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('path', sa.String(length=1000), nullable=False),
    sa.Column('slug', sa.String(length=300), nullable=False),
    sa.Column('url', sa.String(length=255), nullable=False),
    sa.Column('skip_reason', sa.String(length=100), nullable=False),
    sa.Column('seed_source_name', sa.String(length=300), nullable=False),
    sa.Column('seed_phrase', sa.String(length=300), nullable=False),
    sa.Column('form_nominative', sa.String(length=300), nullable=False),
    sa.Column('form_buy', sa.String(length=300), nullable=False),
    sa.Column('form_price', sa.String(length=300), nullable=False),
    sa.Column('chosen_form', sa.String(length=20), nullable=False),
    sa.Column('nominative_count', sa.Integer(), nullable=True),
    sa.Column('declined_count', sa.Integer(), nullable=True),
    sa.Column('total_count', sa.Integer(), nullable=True),
    sa.Column('low_demand', sa.Boolean(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('h1', sa.String(length=255), nullable=False),
    sa.Column('meta_description', sa.Text(), nullable=False),
    sa.Column('meta_keywords', sa.Text(), nullable=False),
    sa.Column('ai_keywords', sa.Text(), nullable=False),
    sa.Column('previous_json', JSON_TYPE, nullable=True),
    sa.Column('remote_metatag_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error_text', sa.Text(), nullable=False),
    sa.Column('wait_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'remote_id', name='uq_category_meta_site_remote')
    )
    op.create_index(op.f('ix_category_meta_site_id'), 'category_meta', ['site_id'], unique=False)

    op.create_table('meta_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('job_run_id', sa.Integer(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('error_text', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['job_run_id'], ['job_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_meta_runs_site_id'), 'meta_runs', ['site_id'], unique=False)

    op.create_table('wordstat_cache',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('phrase', sa.String(length=400), nullable=False),
    sa.Column('region_id', sa.Integer(), nullable=False),
    sa.Column('body', JSON_TYPE, nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'phrase', 'region_id', name='uq_wordstat_cache_key')
    )

    op.create_table('wordstat_calls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('called_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_wordstat_calls_called_at'), 'wordstat_calls', ['called_at'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_wordstat_calls_called_at'), table_name='wordstat_calls')
    op.drop_table('wordstat_calls')
    op.drop_table('wordstat_cache')
    op.drop_index(op.f('ix_meta_runs_site_id'), table_name='meta_runs')
    op.drop_table('meta_runs')
    op.drop_index(op.f('ix_category_meta_site_id'), table_name='category_meta')
    op.drop_table('category_meta')
    op.drop_column('sites', 'brand')
    op.drop_column('sites', 'wordstat_region_id')
    op.drop_column('sites', 'city_in')
    op.drop_column('sites', 'city')
    op.drop_column('sites', 'meta_enabled')
```

- [ ] **Step 9: Проверить миграцию на Postgres**

```bash
docker compose up -d postgres
docker compose run --rm backend sh -c "alembic upgrade head && alembic check && alembic downgrade -1 && alembic upgrade head"
```

Expected: `Running upgrade 4353fdfa69a2 -> c7d2e9a41b30, category meta`, затем `No new upgrade operations detected.`, откат и повторный подъём без ошибок.

- [ ] **Step 10: Commit**

```bash
git add execution/backend/app/clock.py execution/backend/app/models/ execution/backend/alembic/versions/c7d2e9a41b30_category_meta.py execution/backend/tests/test_clock_as_utc.py execution/backend/tests/test_models_category_meta.py
git commit -m "feat: модели и миграция метатегов категорий"
```

---

### Task 2: Настройки Wordstat

**Files:**
- Modify: `execution/backend/app/seed.py`
- Test: `execution/backend/tests/test_category_meta_settings.py`

Эндпоинт настроек (`app/api/admin_settings.py`) правок не требует: он перебирает `DEFAULT_SETTINGS`, `SECRET_KEYS`, `INT_KEYS`, `INT_RANGES`.

- [ ] **Step 1: Тест (красный)**

`tests/test_category_meta_settings.py`:

```python
def test_wordstat_settings_have_defaults(admin_client):
    body = admin_client.get("/api/admin/settings").json()
    assert body["wordstat_hourly_limit"] == "100"
    assert "леруа" in body["meta_stoplist"]
    assert body["wordstat_api_key"] == ""


def test_wordstat_key_is_secret(admin_client):
    admin_client.put("/api/admin/settings", json={"wordstat_api_key": "AQVN-real-secret-key"})
    shown = admin_client.get("/api/admin/settings").json()["wordstat_api_key"]
    assert shown and shown != "AQVN-real-secret-key"


def test_wordstat_hourly_limit_is_validated(admin_client):
    resp = admin_client.put("/api/admin/settings", json={"wordstat_hourly_limit": "0"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_settings.py`
Expected: FAIL — `KeyError: 'wordstat_hourly_limit'`.

- [ ] **Step 3: `app/seed.py`**

В `DEFAULT_SETTINGS` после `"llm_max_retries": "3",`:

```python
    # Квота Yandex Search API на статистику Wordstat — 100 запросов в час
    # (проверено 2026-09-16). Выше — только если Яндекс поднимет квоту.
    "wordstat_hourly_limit": "100",
    # Через запятую; сравнение по подстроке без учёта регистра. Фразы с этими
    # словами не попадают ни в промпт, ни в теги.
    "meta_stoplist": ("леруа, мерлен, лемана, петрович, авито, озон, ozon, wildberries, "
                      "вайлдберриз, яндекс маркет, своими руками"),
```

Заменить три константы:

```python
SECRET_KEYS = {"routerai_api_key", "wordstat_api_key"}
```

```python
INT_KEYS = {"image_workers", "llm_max_retries", "wordstat_hourly_limit"}
```

```python
INT_RANGES = {"image_workers": (1, 8), "llm_max_retries": (1, 3),
              "wordstat_hourly_limit": (1, 100000)}
```

- [ ] **Step 4: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_settings.py tests/test_api_admin_settings.py tests/test_settings.py tests/test_ai_factory.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/seed.py execution/backend/tests/test_category_meta_settings.py
git commit -m "feat: настройки Wordstat — ключ, квота в час, стоп-лист"
```

---

### Task 3: Клиент Wordstat

**Files:**
- Create: `execution/backend/app/wordstat/__init__.py` (пустой)
- Create: `execution/backend/app/wordstat/client.py`
- Test: `execution/backend/tests/test_wordstat_client.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_wordstat_client.py`:

```python
import json

import pytest
import requests

from app.wordstat.client import (
    WordstatAuthError, WordstatClient, WordstatError, parse_top,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", json_error=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload if payload is not None else {}
        self._json_error = json_error
        self.text = text

    def json(self):
        if self._json_error:
            raise json.JSONDecodeError("Expecting value", self.text, 0)
        return self._payload


def scripted_post(monkeypatch, responses):
    """Каждый вызов requests.post отдаёт следующий элемент; исключение — поднимается."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        item = responses[min(len(calls), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("app.wordstat.client.requests.post", fake_post)
    return calls


def client(sleeps=None):
    return WordstatClient("KEY", sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_top_requests_sends_key_region_and_phrase_count(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(payload={"totalCount": "5"})])
    assert client().top_requests("фанера", 213, 300) == {"totalCount": "5"}
    call = calls[0]
    assert call["url"] == "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
    assert call["headers"] == {"Authorization": "Api-Key KEY"}
    assert call["json"] == {"phrase": "фанера", "numPhrases": "300", "regions": ["213"]}
    assert call["timeout"] == 30


def test_top_requests_without_region_omits_regions(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse()])
    client().top_requests("фанера", None, 1)
    assert "regions" not in calls[0]["json"]


def test_regions_tree_posts_empty_body(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(payload={"regions": []})])
    assert client().regions_tree() == {"regions": []}
    # Метод называется getRegionsTree: «regionsTree» отвечает 404 (проверено 2026-09-16).
    assert calls[0]["url"] == "https://searchapi.api.cloud.yandex.net/v2/wordstat/getRegionsTree"
    assert calls[0]["json"] == {}


def test_auth_error_is_not_retried(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(401, text="bad key")])
    with pytest.raises(WordstatAuthError) as err:
        client().top_requests("фанера", 213)
    assert len(calls) == 1
    assert err.value.status_code == 401
    assert "wordstat_api_key" in str(err.value)


def test_bad_request_is_not_retried(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(400, text="phrase too long")])
    with pytest.raises(WordstatError) as err:
        client().top_requests("фанера", 213)
    assert len(calls) == 1 and err.value.status_code == 400
    assert not isinstance(err.value, WordstatAuthError)


def test_rate_limit_is_retried_with_backoff(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(429), FakeResponse(payload={"ok": 1})])
    sleeps = []
    assert client(sleeps).top_requests("фанера", 213) == {"ok": 1}
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_server_error_gives_up_after_three_attempts(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(503, text="down")])
    sleeps = []
    with pytest.raises(WordstatError) as err:
        client(sleeps).top_requests("фанера", 213)
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]
    assert err.value.status_code == 503


def test_network_failure_is_retried_and_reported(monkeypatch):
    calls = scripted_post(monkeypatch, [requests.ConnectionError("dns")])
    with pytest.raises(WordstatError) as err:
        client().top_requests("фанера", 213)
    assert len(calls) == 3
    assert err.value.status_code is None
    assert "сеть недоступна" in str(err.value)


def test_non_json_body_is_retried(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(json_error=True, text="<html>"),
                                        FakeResponse(payload={"ok": 1})])
    assert client().top_requests("фанера", 213) == {"ok": 1}
    assert len(calls) == 2


def test_parse_top_converts_strings():
    result = parse_top({"totalCount": "96275",
                        "results": [{"phrase": "фанера", "count": "96275"},
                                    {"phrase": "фанера купить", "count": "12238"},
                                    {"phrase": "", "count": "1"}]})
    assert result.total_count == 96275
    assert result.phrases == [("фанера", 96275), ("фанера купить", 12238)]


def test_parse_top_empty_body():
    result = parse_top({})
    assert result.total_count == 0 and result.phrases == []
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_wordstat_client.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.wordstat'`.

- [ ] **Step 3: Реализация**

Создать пустой `app/wordstat/__init__.py` и `app/wordstat/client.py`:

```python
"""Клиент Yandex Search API — Wordstat.

Проверено 2026-09-16 (directions/2026-09-16-category-meta-design.md):
- POST {base}/topRequests и {base}/getRegionsTree, `Authorization: Api-Key ...`
  (именно getRegionsTree: «regionsTree» отвечает 404);
  folderId не нужен — папка берётся из сервисного аккаунта ключа;
- числа в ответе приходят строками;
- квота 100 запросов в час — считает её не клиент, а app/wordstat/quota.py.

Ретраи — здесь, в отличие от SiteClient: запросы только читающие, повтор
безопасен. Повторяются сетевые сбои, 429 и 5xx; 401/403 — WordstatAuthError
(ключ не подходит, запуск останавливается), прочие 4xx — сразу отказ.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

WORDSTAT_BASE_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat"
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0


class WordstatError(RuntimeError):
    """status_code — HTTP-код; None для сетевых сбоев и ответа не-JSON."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class WordstatAuthError(WordstatError):
    """401/403: ключ не подходит — повтор не поможет."""


@dataclass
class TopResult:
    total_count: int
    phrases: list[tuple[str, int]]


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_top(body: dict) -> TopResult:
    phrases = [(str(item.get("phrase", "")).strip(), _int(item.get("count")))
               for item in (body.get("results") or []) if item.get("phrase")]
    return TopResult(total_count=_int(body.get("totalCount")), phrases=phrases)


class WordstatClient:
    def __init__(self, api_key: str, *, base_url: str = WORDSTAT_BASE_URL,
                 timeout: int = TIMEOUT_SECONDS, max_attempts: int = MAX_ATTEMPTS,
                 backoff: float = BACKOFF_SECONDS, sleep=time.sleep):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff = backoff
        self.sleep = sleep

    def top_requests(self, phrase: str, region_id: int | None, num_phrases: int = 300) -> dict:
        payload: dict = {"phrase": phrase, "numPhrases": str(num_phrases)}
        if region_id:
            payload["regions"] = [str(region_id)]
        return self._post("topRequests", payload)

    def regions_tree(self) -> dict:
        return self._post("getRegionsTree", {})

    def _post(self, method: str, payload: dict) -> dict:
        url = f"{self.base_url}/{method}"
        headers = {"Authorization": f"Api-Key {self.api_key}"}
        last_error = WordstatError(f"Wordstat {method}: не выполнено ни одной попытки")
        for attempt in range(self.max_attempts):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = WordstatError(
                    f"Wordstat {method}: сеть недоступна: {type(exc).__name__}: {exc}")
            else:
                if response.status_code in (401, 403):
                    raise WordstatAuthError(
                        f"ключ Wordstat отклонён — проверьте настройку wordstat_api_key "
                        f"(HTTP {response.status_code}): {response.text[:200]}",
                        response.status_code)
                if response.ok:
                    try:
                        return response.json()
                    except ValueError:
                        last_error = WordstatError(
                            f"Wordstat {method}: ответ не JSON: {response.text[:200]}")
                elif response.status_code == 429 or response.status_code >= 500:
                    last_error = WordstatError(
                        f"Wordstat {method}: HTTP {response.status_code}: {response.text[:200]}",
                        response.status_code)
                else:
                    raise WordstatError(
                        f"Wordstat {method}: HTTP {response.status_code}: {response.text[:200]}",
                        response.status_code)
            if attempt < self.max_attempts - 1:
                self.sleep(self.backoff * (2 ** attempt))
        raise last_error
```

- [ ] **Step 4: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_wordstat_client.py`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/wordstat/ execution/backend/tests/test_wordstat_client.py
git commit -m "feat: клиент Wordstat с повторами и разбором ответа"
```

---

### Task 4: Ограничитель квоты Wordstat

**Files:**
- Create: `execution/backend/app/wordstat/quota.py`
- Test: `execution/backend/tests/test_wordstat_quota.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_wordstat_quota.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.category_meta import WordstatCall
from app.wordstat.quota import QuotaExceeded, reserve

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def calls_count(db):
    return db.scalar(select(func.count()).select_from(WordstatCall))


def add_calls(db, count, minutes_ago):
    db.add_all([WordstatCall(called_at=NOW - timedelta(minutes=minutes_ago)) for _ in range(count)])
    db.commit()


def test_reserve_under_limit_records_calls(db_session):
    assert reserve(db_session, 3, 100, NOW) == 0.0
    assert calls_count(db_session) == 3


def test_reserve_zero_does_nothing(db_session):
    assert reserve(db_session, 0, 100, NOW) == 0.0
    assert calls_count(db_session) == 0


def test_reserve_exactly_to_limit(db_session):
    add_calls(db_session, 97, minutes_ago=10)
    assert reserve(db_session, 3, 100, NOW) == 0.0
    assert calls_count(db_session) == 100


def test_over_limit_returns_wait_until_oldest_needed_slot_frees(db_session):
    add_calls(db_session, 1, minutes_ago=50)
    add_calls(db_session, 99, minutes_ago=40)
    # Нужен 1 слот: освободится, когда запросу 50-минутной давности исполнится час.
    assert reserve(db_session, 1, 100, NOW) == pytest.approx(600)
    # Нужно 3 слота: второй и третий по старшинству — 40-минутные, ждать 20 минут.
    assert reserve(db_session, 3, 100, NOW) == pytest.approx(1200)
    assert calls_count(db_session) == 100


def test_calls_older_than_hour_do_not_count(db_session):
    add_calls(db_session, 100, minutes_ago=61)
    assert reserve(db_session, 3, 100, NOW) == 0.0


def test_calls_older_than_two_hours_are_deleted(db_session):
    add_calls(db_session, 5, minutes_ago=121)
    add_calls(db_session, 2, minutes_ago=90)
    reserve(db_session, 1, 100, NOW)
    assert calls_count(db_session) == 3


def test_count_above_limit_is_programming_error(db_session):
    with pytest.raises(ValueError):
        reserve(db_session, 3, 2, NOW)


def test_quota_exceeded_carries_seconds():
    assert QuotaExceeded(125.5).seconds == 125.5
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_wordstat_quota.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.wordstat.quota'`.

- [ ] **Step 3: Реализация — `app/wordstat/quota.py`**

```python
"""Глобальный ограничитель запросов к Wordstat — один на все проекты.

Квота Яндекса — 100 запросов в час. Считаем по журналу wordstat_calls за
скользящий час. Резервирование — под advisory-блокировкой Postgres: два
воркера одновременно не превысят лимит. В SQLite (тесты) блокировки нет — там
один поток.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.clock import as_utc, utcnow
from app.models.category_meta import WordstatCall

WINDOW = timedelta(hours=1)
LOCK_KEY = 20260916


class QuotaExceeded(RuntimeError):
    """Квоты на этот час нет; seconds — через сколько освободится нужное число слотов."""

    def __init__(self, seconds: float):
        super().__init__(f"квота Wordstat исчерпана, освободится через {int(seconds)} с")
        self.seconds = seconds


def _lock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})


def reserve(db: Session, count: int, limit: int, now: datetime | None = None) -> float:
    """0.0 — слоты зарезервированы (строки журнала записаны и закоммичены);
    больше нуля — столько секунд ждать, ничего не записано."""
    if count <= 0:
        return 0.0
    if count > limit:
        raise ValueError(f"нельзя зарезервировать {count} запросов при лимите {limit} в час")
    now = now or utcnow()
    _lock(db)
    db.execute(delete(WordstatCall).where(WordstatCall.called_at < now - 2 * WINDOW))
    calls = [as_utc(moment) for moment in db.scalars(
        select(WordstatCall.called_at)
        .where(WordstatCall.called_at > now - WINDOW)
        .order_by(WordstatCall.called_at)).all()]
    overflow = len(calls) + count - limit
    if overflow <= 0:
        db.add_all([WordstatCall(called_at=now) for _ in range(count)])
        db.commit()
        return 0.0
    db.commit()   # фиксирует чистку и отпускает блокировку
    frees_at = calls[overflow - 1] + WINDOW
    return max(1.0, (frees_at - now).total_seconds())
```

- [ ] **Step 4: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_wordstat_quota.py`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/wordstat/quota.py execution/backend/tests/test_wordstat_quota.py
git commit -m "feat: глобальный ограничитель запросов к Wordstat — 100 в час"
```

---

### Task 5: Кеш, регионы и фабрика Wordstat

**Files:**
- Create: `execution/backend/app/wordstat/cache.py`, `app/wordstat/regions.py`, `app/wordstat/factory.py`
- Test: `execution/backend/tests/test_wordstat_cache.py`, `tests/test_wordstat_regions.py`, `tests/test_wordstat_factory.py`

- [ ] **Step 1: Тесты (красные)**

`tests/test_wordstat_cache.py`:

```python
from datetime import datetime, timedelta, timezone

from app.wordstat.cache import cache_get, cache_put

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def test_miss_then_hit(db_session):
    assert cache_get(db_session, "top", "фанера", 213, NOW) is None
    cache_put(db_session, "top", "фанера", 213, {"totalCount": "5"}, NOW)
    assert cache_get(db_session, "top", "фанера", 213, NOW) == {"totalCount": "5"}


def test_key_includes_kind_and_region(db_session):
    cache_put(db_session, "top", "фанера", 213, {"a": 1}, NOW)
    assert cache_get(db_session, "exact", "фанера", 213, NOW) is None
    assert cache_get(db_session, "top", "фанера", 2, NOW) is None


def test_expired_after_30_days(db_session):
    cache_put(db_session, "top", "фанера", 213, {"a": 1}, NOW - timedelta(days=31))
    assert cache_get(db_session, "top", "фанера", 213, NOW) is None
    assert cache_get(db_session, "top", "фанера", 213, NOW - timedelta(days=2)) == {"a": 1}


def test_put_overwrites(db_session):
    cache_put(db_session, "top", "фанера", 213, {"a": 1}, NOW - timedelta(days=31))
    cache_put(db_session, "top", "фанера", 213, {"a": 2}, NOW)
    assert cache_get(db_session, "top", "фанера", 213, NOW) == {"a": 2}


def test_none_region_is_zero(db_session):
    cache_put(db_session, "regions", "", None, {"regions": []}, NOW)
    assert cache_get(db_session, "regions", "", 0, NOW) == {"regions": []}
```

`tests/test_wordstat_regions.py`:

```python
import pytest
from sqlalchemy import func, select

from app.models.category_meta import WordstatCall
from app.wordstat.cache import cache_put
from app.wordstat.quota import QuotaExceeded
from app.wordstat.regions import Region, flatten_regions, load_regions, search_regions

TREE = {"regions": [{"id": "225", "label": "Россия", "children": [
    {"id": "1", "label": "Москва и Московская область", "children": [
        {"id": "213", "label": "Москва", "children": [{"id": "216", "label": "Зеленоград"}]},
        {"id": "10716", "label": "Балашиха"},
    ]},
    {"id": "192", "label": "Владимир"},
    {"id": "bad", "label": "Без id"},
]}]}


def test_flatten_builds_paths():
    regions = flatten_regions(TREE)
    moscow = next(r for r in regions if r.id == 213)
    assert moscow.path == "Россия / Москва и Московская область / Москва"
    assert Region(216, "Зеленоград",
                  "Россия / Москва и Московская область / Москва / Зеленоград") in regions
    assert all(r.label != "Без id" for r in regions)


def test_search_exact_first_then_prefix():
    found = search_regions(flatten_regions(TREE), " москва ")
    assert [r.id for r in found] == [213, 1]


def test_search_empty_query():
    assert search_regions(flatten_regions(TREE), "  ") == []


class FakeWordstat:
    def __init__(self):
        self.calls = 0

    def regions_tree(self):
        self.calls += 1
        return TREE


def test_load_regions_fetches_once_and_caches(db_session):
    fake = FakeWordstat()
    assert len(load_regions(db_session, 100, lambda: fake)) == 6
    assert len(load_regions(db_session, 100, lambda: fake)) == 6
    assert fake.calls == 1
    assert db_session.scalar(select(func.count()).select_from(WordstatCall)) == 1


def test_load_regions_from_cache_does_not_build_client(db_session):
    cache_put(db_session, "regions", "", 0, TREE)

    def no_client():
        raise AssertionError("клиент не нужен — дерево в кеше")

    assert len(load_regions(db_session, 100, no_client)) == 6


def test_load_regions_quota_exhausted(db_session):
    fake = FakeWordstat()
    load_regions_limit = 1
    db_session.add(WordstatCall())
    db_session.commit()
    with pytest.raises(QuotaExceeded):
        load_regions(db_session, load_regions_limit, lambda: fake)
    assert fake.calls == 0
```

`tests/test_wordstat_factory.py`:

```python
import pytest

from app.config import config
from app.settings.service import SettingsService
from app.wordstat.factory import (
    WordstatConfigError, build_wordstat_client, hourly_limit, stoplist,
)


@pytest.fixture
def settings(db_session, monkeypatch):
    monkeypatch.setattr(config, "encryption_key", "8Bq3mA0kXqL2pR7vT1yZ4nC6wE9sU5hJ0dF2gK8lM3o=")
    return SettingsService(db_session, config.encryption_key)


def test_client_requires_key(db_session, settings):
    with pytest.raises(WordstatConfigError):
        build_wordstat_client(db_session)


def test_client_uses_decrypted_key(db_session, settings):
    settings.set_secret("wordstat_api_key", "AQVN-key")
    assert build_wordstat_client(db_session).api_key == "AQVN-key"


def test_hourly_limit_default_and_bounds(db_session, settings):
    assert hourly_limit(db_session) == 100
    settings.set("wordstat_hourly_limit", "0")
    assert hourly_limit(db_session) == 1
    settings.set("wordstat_hourly_limit", "abc")
    assert hourly_limit(db_session) == 100


def test_stoplist_default_and_custom(db_session, settings):
    assert "леруа" in stoplist(db_session)
    settings.set("meta_stoplist", " Леруа ,, Петрович ")
    assert stoplist(db_session) == ["леруа", "петрович"]
```

- [ ] **Step 2: Убедиться, что падают**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_wordstat_cache.py tests/test_wordstat_regions.py tests/test_wordstat_factory.py`
Expected: FAIL — `ModuleNotFoundError` для `app.wordstat.cache`, `app.wordstat.regions`, `app.wordstat.factory`.

- [ ] **Step 3: `app/wordstat/cache.py`**

```python
"""Кеш ответов Wordstat. Статистика за 30 дней меняется медленно, а квота —
100 запросов в час, поэтому перегенерация категории берёт данные отсюда."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc, utcnow
from app.models.category_meta import WordstatCache

CACHE_TTL = timedelta(days=30)


def _row(db: Session, kind: str, phrase: str, region_id: int | None) -> WordstatCache | None:
    return db.scalars(select(WordstatCache).where(
        WordstatCache.kind == kind, WordstatCache.phrase == phrase,
        WordstatCache.region_id == (region_id or 0))).first()


def cache_get(db: Session, kind: str, phrase: str, region_id: int | None,
              now: datetime | None = None) -> dict | None:
    row = _row(db, kind, phrase, region_id)
    if row is None or as_utc(row.fetched_at) < (now or utcnow()) - CACHE_TTL:
        return None
    return row.body


def cache_put(db: Session, kind: str, phrase: str, region_id: int | None, body: dict,
              now: datetime | None = None) -> None:
    row = _row(db, kind, phrase, region_id)
    if row is None:
        row = WordstatCache(kind=kind, phrase=phrase, region_id=region_id or 0)
        db.add(row)
    row.body = body
    row.fetched_at = now or utcnow()
    db.commit()
```

- [ ] **Step 4: `app/wordstat/regions.py`**

```python
"""Справочник регионов Wordstat: поиск id региона по названию города."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.wordstat.cache import cache_get, cache_put
from app.wordstat.quota import QuotaExceeded, reserve

REGIONS_KIND = "regions"


@dataclass
class Region:
    id: int
    label: str
    path: str      # «Россия / Центр / Москва и Московская область / Москва»


def flatten_regions(tree: dict) -> list[Region]:
    result: list[Region] = []

    def walk(nodes, parents: list[str]) -> None:
        for node in nodes or []:
            label = str(node.get("label", ""))
            try:
                region_id = int(node.get("id"))
            except (TypeError, ValueError):
                region_id = None
            if region_id is not None:
                result.append(Region(region_id, label, " / ".join([*parents, label])))
            walk(node.get("children"), [*parents, label])

    walk(tree.get("regions"), [])
    return result


def search_regions(regions: list[Region], query: str, limit: int = 10) -> list[Region]:
    """Сначала точные совпадения названия, затем начинающиеся с запроса."""
    needle = query.strip().casefold()
    if not needle:
        return []
    exact = [r for r in regions if r.label.casefold() == needle]
    prefix = [r for r in regions if r.label.casefold().startswith(needle)
              and r.label.casefold() != needle]
    return (exact + prefix)[:limit]


def load_regions(db: Session, limit: int, client_factory) -> list[Region]:
    """Дерево регионов из кеша; при промахе — один запрос к Wordstat из квоты.
    client_factory() зовётся ДО резервирования: без ключа квота не тратится."""
    body = cache_get(db, REGIONS_KIND, "", 0)
    if body is None:
        client = client_factory()
        wait = reserve(db, 1, limit)
        if wait:
            raise QuotaExceeded(wait)
        body = client.regions_tree()
        cache_put(db, REGIONS_KIND, "", 0, body)
    return flatten_regions(body)
```

- [ ] **Step 5: `app/wordstat/factory.py`**

```python
"""Сборка клиента Wordstat и его настроек из БД — по образцу app/ai/factory.py."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import config
from app.seed import DEFAULT_SETTINGS, INT_RANGES
from app.settings.crypto import SecretDecryptionError
from app.settings.service import SettingsService
from app.wordstat.client import WordstatClient

logger = logging.getLogger(__name__)


class WordstatConfigError(RuntimeError):
    """Ключ не задан или зашифрован другим ENCRYPTION_KEY — чинится в настройках."""


def _service(db: Session) -> SettingsService:
    return SettingsService(db, config.encryption_key)


def build_wordstat_client(db: Session) -> WordstatClient:
    try:
        key = _service(db).get_secret("wordstat_api_key")
    except SecretDecryptionError as exc:
        raise WordstatConfigError(str(exc)) from exc
    if not key:
        raise WordstatConfigError(
            "ключ Wordstat не задан — заполните wordstat_api_key в настройках")
    return WordstatClient(key)


def hourly_limit(db: Session) -> int:
    default = int(DEFAULT_SETTINGS["wordstat_hourly_limit"])
    low, high = INT_RANGES["wordstat_hourly_limit"]
    try:
        value = _service(db).get_int("wordstat_hourly_limit", default)
    except ValueError:
        logger.warning("wordstat_hourly_limit не число — используем %s", default)
        value = default
    return max(low, min(high, value))


def stoplist(db: Session) -> list[str]:
    raw = _service(db).get_str("meta_stoplist", DEFAULT_SETTINGS["meta_stoplist"])
    return [word.strip().casefold() for word in raw.split(",") if word.strip()]
```

- [ ] **Step 6: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_wordstat_cache.py tests/test_wordstat_regions.py tests/test_wordstat_factory.py`
Expected: PASS (15 passed).

- [ ] **Step 7: Commit**

```bash
git add execution/backend/app/wordstat/ execution/backend/tests/test_wordstat_cache.py execution/backend/tests/test_wordstat_regions.py execution/backend/tests/test_wordstat_factory.py
git commit -m "feat: кеш Wordstat на 30 дней, поиск региона по городу"
```

---

### Task 6: Выбор словоформы

**Files:**
- Create: `execution/backend/app/category_meta/__init__.py` (пустой)
- Create: `execution/backend/app/category_meta/forms.py`
- Test: `execution/backend/tests/test_category_meta_forms.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_category_meta_forms.py`:

```python
from app.category_meta.forms import (
    buy_queries, choose_form, declined_object, exact_query, filter_stoplist, forms_differ,
    sell_word,
)


def test_exact_query_marks_every_word():
    assert exact_query("Купить  гибкую черепицу") == '"!купить !гибкую !черепицу"'


def test_buy_queries_nominative_then_declined():
    assert buy_queries("фанера", "купить фанеру") == ('"!купить !фанера"', '"!купить !фанеру"')


def test_declined_object_strips_buy_prefix():
    assert declined_object("Купить фанеру") == "фанеру"
    assert declined_object("фанеру") == "фанеру"


def test_forms_differ():
    assert forms_differ("фанера", "купить фанеру")
    assert not forms_differ("металлический уголок", "купить металлический уголок")
    assert not forms_differ("Профнастил", "купить  профнастил")


def test_choose_form_by_frequency():
    assert choose_form(678, 234) == "nominative"
    assert choose_form(85, 120) == "declined"


def test_choose_form_falls_back_to_declined():
    assert choose_form(9, 3) == "declined"        # обе частоты ниже порога
    assert choose_form(50, 50) == "declined"      # ничья
    assert choose_form(None, None) == "declined"  # формы совпадают, проверки не было


def test_sell_word():
    assert sell_word([("фанера купить", 12238), ("фанера цена", 5300)]) == "купить"
    assert sell_word([("профнастил цена", 900), ("купить профнастил", 100)]) == "цена"
    assert sell_word([]) == "купить"


def test_filter_stoplist():
    phrases = [("фанера", 10), ("леруа фанера", 5), ("фанера Петрович", 3)]
    assert filter_stoplist(phrases, ["леруа", "петрович"]) == [("фанера", 10)]
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_forms.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.category_meta'`.

- [ ] **Step 3: Реализация**

Создать пустой `app/category_meta/__init__.py` и `app/category_meta/forms.py`:

```python
"""Выбор словоформы названия категории по статистике Wordstat.

Решение принимает код, а не LLM: модель получает готовую форму явной
инструкцией (directions/2026-09-16-category-meta-design.md, «Правила генерации»).
"""

from __future__ import annotations

BUY_PREFIX = "купить "

# Ниже этой частоты у обеих форм статистика ничего не говорит — берём
# грамотную склонённую форму.
MIN_FORM_COUNT = 10

# Меньше стольких запросов в месяц по фразе — категория помечается «мало данных».
LOW_DEMAND_THRESHOLD = 50


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def declined_object(form_buy: str) -> str:
    """«купить фанеру» → «фанеру». Без префикса — фраза как есть."""
    norm = " ".join((form_buy or "").split())
    if norm.casefold().startswith(BUY_PREFIX):
        return norm[len(BUY_PREFIX):]
    return norm


def forms_differ(form_nominative: str, form_buy: str) -> bool:
    """«купить профнастил» не склоняется — проверять в Wordstat нечего."""
    return _norm(form_nominative) != _norm(declined_object(form_buy))


def exact_query(phrase: str) -> str:
    """Точная форма для Wordstat: кавычки фиксируют число слов, «!» — словоформу
    каждого слова. «купить фанеру» → "!купить !фанеру"."""
    return '"' + " ".join(f"!{word}" for word in _norm(phrase).split()) + '"'


def buy_queries(form_nominative: str, form_buy: str) -> tuple[str, str]:
    """(несклонённая, склонённая) — «купить фанера» и «купить фанеру»."""
    return (exact_query(f"{BUY_PREFIX}{form_nominative}"),
            exact_query(f"{BUY_PREFIX}{declined_object(form_buy)}"))


def choose_form(nominative_count: int | None, declined_count: int | None) -> str:
    if nominative_count is None or declined_count is None:
        return "declined"
    if max(nominative_count, declined_count) < MIN_FORM_COUNT:
        return "declined"
    if nominative_count == declined_count:
        return "declined"
    return "nominative" if nominative_count > declined_count else "declined"


def sell_word(phrases: list[tuple[str, int]]) -> str:
    """Какое продающее слово чаще в запросах: «купить» или «цена»."""
    buy = sum(count for phrase, count in phrases if "купит" in phrase.casefold())
    price = sum(count for phrase, count in phrases if "цен" in phrase.casefold())
    return "цена" if price > buy else "купить"


def filter_stoplist(phrases: list[tuple[str, int]],
                    stoplist: list[str]) -> list[tuple[str, int]]:
    return [(phrase, count) for phrase, count in phrases
            if not any(stop in phrase.casefold() for stop in stoplist)]
```

- [ ] **Step 4: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_forms.py`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/category_meta/ execution/backend/tests/test_category_meta_forms.py
git commit -m "feat: выбор словоформы названия категории по частотам Wordstat"
```

---

### Task 7: Проверка тегов

**Files:**
- Create: `execution/backend/app/category_meta/validate.py`
- Test: `execution/backend/tests/test_category_meta_validate.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_category_meta_validate.py`:

```python
import pytest

from app.category_meta.validate import (
    MetaContext, allowed_keywords, normalize_tags, split_phrases, validate_tags,
)

PHRASES = ["фанера", "фанера купить", "фанера цена", "фанера москва",
           "купить фанеру в москве", "лист фанеры цена", "фанера влагостойкая",
           "ламинированная фанера", "фанера фсф", "фанера фк"]


def ctx(**overrides):
    values = dict(form_nominative="фанера", form_buy="купить фанеру",
                  chosen_form="nominative", city="Москва", city_in="в Москве",
                  brand="Стройбаза", wordstat_phrases=PHRASES,
                  stoplist=["леруа", "петрович"],
                  site_description="Интернет-магазин стройматериалов, доставка по Москве")
    values.update(overrides)
    return MetaContext(**values)


def valid_tags(**overrides):
    tags = {
        "title": "Фанера в Москве — купить по выгодной цене | Стройбаза",
        "h1": "Фанера в Москве",
        "meta_description": ("Фанера в Москве по выгодной цене: влагостойкая ФСФ, ФК, "
                             "ламинированная и берёзовая. Листы 1525×1525 и 2440×1220 мм, "
                             "толщина 4–40 мм. Доставка по Москве."),
        "meta_keywords": ("фанера, фанера купить, фанера цена, фанера москва, "
                          "купить фанеру в москве, лист фанеры цена, фанера влагостойкая, "
                          "ламинированная фанера, фанера фсф, фанера фк"),
        "ai_keywords": ("где купить фанеру в Москве, какая фанера подходит для пола, "
                        "влагостойкая фанера ФСФ цена, фанера ФК или ФСФ что выбрать, "
                        "ламинированная фанера для опалубки, фанера 18 мм купить в Москве, "
                        "лист фанеры 1525×1525 цена, берёзовая фанера для мебели, "
                        "фанера с доставкой по Москве, сколько стоит лист фанеры"),
    }
    tags.update(overrides)
    return tags


def test_valid_tags_pass():
    assert validate_tags(valid_tags(), ctx()) == []


def test_declined_form_requires_title_to_start_with_buy_form():
    tags = valid_tags(title="Купить фанеру в Москве по выгодной цене | Стройбаза")
    assert validate_tags(tags, ctx(chosen_form="declined")) == []
    errors = validate_tags(valid_tags(), ctx(chosen_form="declined"))
    assert "title должен начинаться с «купить фанеру»" in errors


def test_title_rules():
    long_title = "Фанера в Москве — купить по выгодной цене, большой выбор листов | Стройбаза"
    errors = validate_tags(valid_tags(title=long_title), ctx())
    assert any(e.startswith("title длиннее 70") for e in errors)
    errors = validate_tags(valid_tags(title="Фанера в Москве — купить по выгодной цене"), ctx())
    assert "title должен оканчиваться на « | Стройбаза»" in errors
    errors = validate_tags(valid_tags(title="Фанера — купить по выгодной цене | Стройбаза"), ctx())
    assert "в title нет «в Москве»" in errors
    errors = validate_tags(valid_tags(title="Фанера в Москве — большой выбор | Стройбаза"), ctx())
    assert "в title нет продающего слова «купить» или «цена»" in errors


def test_h1_rules():
    assert "в h1 не должно быть бренда" in validate_tags(
        valid_tags(h1="Фанера в Москве — Стройбаза"), ctx())
    assert "в h1 нет «в Москве»" in validate_tags(valid_tags(h1="Фанера"), ctx())
    assert "h1 не должен повторять title" in validate_tags(
        valid_tags(h1="Фанера в Москве — купить по выгодной цене"), ctx())
    assert "h1 пустой" in validate_tags(valid_tags(h1=""), ctx())


def test_description_length_and_city():
    errors = validate_tags(valid_tags(meta_description="Фанера в Москве."), ctx())
    assert any(e.startswith("description должен быть 140–170") for e in errors)
    no_city = valid_tags()["meta_description"].replace("в Москве", "в городе")
    assert "в description нет «в Москве»" in validate_tags(valid_tags(meta_description=no_city), ctx())


def test_description_forbidden_claims_unless_site_says_so():
    text = ("Фанера в Москве в наличии на складе: влагостойкая ФСФ, ФК, ламинированная и "
            "берёзовая. Листы 1525×1525 и 2440×1220 мм, толщина 4–40 мм, доставка.")
    tags = valid_tags(meta_description=text)
    assert "в description утверждение «в наличии», которого нет в описании сайта" in \
        validate_tags(tags, ctx())
    assert validate_tags(tags, ctx(site_description="Всё в наличии на складе в Москве")) == []


def test_opt_matches_whole_word_only():
    text = ("Фанера в Москве — оптимальный выбор для пола и стен: влагостойкая ФСФ, ФК, "
            "ламинированная. Листы 1525×1525 и 2440×1220 мм, толщина 4–40 мм.")
    assert not any("оптом" in e for e in validate_tags(valid_tags(meta_description=text), ctx()))


def test_keywords_limit_and_source():
    eleven = valid_tags()["meta_keywords"] + ", фанера москва"
    assert "keywords: больше 10 фраз (11)" in validate_tags(valid_tags(meta_keywords=eleven), ctx())
    errors = validate_tags(valid_tags(meta_keywords="фанера, фанера оптом дешево"), ctx())
    assert "keywords не из статистики Wordstat: фанера оптом дешево" in errors
    assert "keywords пустые" in validate_tags(valid_tags(meta_keywords=""), ctx())


def test_city_variants_are_allowed_keywords():
    allowed = allowed_keywords(ctx(wordstat_phrases=[]))
    assert {"фанера москва", "фанера в москве", "купить фанеру в москве"} <= allowed


def test_ai_keywords_count():
    errors = validate_tags(valid_tags(ai_keywords="где купить фанеру"), ctx())
    assert "ai_keywords: нужно 10–15 фраз (1)" in errors


def test_stoplist_in_any_field():
    tags = valid_tags(ai_keywords=valid_tags()["ai_keywords"] + ", фанера в леруа")
    assert "в ai_keywords слова из стоп-листа: леруа" in validate_tags(tags, ctx())


def test_normalize_tags_joins_lists_and_lowercases_keywords():
    tags = normalize_tags({"title": "  Фанера   в Москве ", "h1": "Фанера",
                           "meta_description": "x", "meta_keywords": ["Фанера", "Фанера Цена"],
                           "ai_keywords": "Где купить,  фанеру ,"})
    assert tags["title"] == "Фанера в Москве"
    assert tags["meta_keywords"] == "фанера, фанера цена"
    assert tags["ai_keywords"] == "Где купить, фанеру"


def test_normalize_tags_capitalizes_visible_fields():
    """2026-09-16 на живой проверке модель вернула title «купить профнастил в Москве…»."""
    tags = normalize_tags({"title": "купить профнастил в Москве | Стройбаза", "h1": "профнастил",
                           "meta_description": "профнастил в Москве", "meta_keywords": "профнастил",
                           "ai_keywords": "где купить профнастил"})
    assert tags["title"] == "Купить профнастил в Москве | Стройбаза"
    assert tags["h1"] == "Профнастил"
    assert tags["meta_description"] == "Профнастил в Москве"
    assert tags["meta_keywords"] == "профнастил"
    assert tags["ai_keywords"] == "где купить профнастил"


def test_normalize_tags_rejects_non_object():
    with pytest.raises(ValueError):
        normalize_tags(["title"])


def test_split_phrases_drops_empty():
    assert split_phrases("а, , б ,") == ["а", "б"]
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_validate.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.category_meta.validate'`.

- [ ] **Step 3: Реализация — `app/category_meta/validate.py`**

```python
"""Проверка тегов, которые вернула LLM. Правила — таблица «Правила полей» в
directions/2026-09-16-category-meta-design.md. Каждое нарушение — строка по-русски:
она уходит обратно в модель при повторе и в error_text категории."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TITLE_MAX = 70
H1_MAX = 60
DESCRIPTION_MIN = 140
DESCRIPTION_MAX = 170
KEYWORDS_MAX = 10
AI_KEYWORDS_MIN = 10
AI_KEYWORDS_MAX = 15

TAG_FIELDS = ("title", "h1", "meta_description", "meta_keywords", "ai_keywords")

# Утверждения о магазине, которые нельзя писать, если их нет в описании сайта.
# «опт» — словами целиком: подстрока «опт» есть в «оптимальный».
FORBIDDEN_CLAIMS = {
    "в наличии": r"в\s+наличии",
    "скидки": r"скидк",
    "оптом": r"\bопт(ом|ов\w*)\b",
}


@dataclass
class MetaContext:
    form_nominative: str
    form_buy: str
    chosen_form: str               # nominative | declined
    city: str                      # «Москва»
    city_in: str                   # «в Москве»
    brand: str
    wordstat_phrases: list[str] = field(default_factory=list)
    stoplist: list[str] = field(default_factory=list)
    site_description: str = ""


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def split_phrases(text: str) -> list[str]:
    return [" ".join(part.split()) for part in (text or "").split(",") if part.strip()]


def normalize_tags(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("модель вернула не JSON-объект с тегами")
    tags = {}
    for name in TAG_FIELDS:
        value = raw.get(name, "")
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        tags[name] = " ".join(str(value or "").split())
    # Видимые поля — с заглавной: модель иногда начинает title со строчной
    # («купить профнастил в Москве…», живая проверка 2026-09-16).
    for name in ("title", "h1", "meta_description"):
        tags[name] = tags[name][:1].upper() + tags[name][1:]
    tags["meta_keywords"] = ", ".join(p.casefold() for p in split_phrases(tags["meta_keywords"]))
    tags["ai_keywords"] = ", ".join(split_phrases(tags["ai_keywords"]))
    return tags


def allowed_keywords(ctx: MetaContext) -> set[str]:
    nominative, city_in = _norm(ctx.form_nominative), _norm(ctx.city_in)
    allowed = {_norm(phrase) for phrase in ctx.wordstat_phrases}
    allowed |= {f"{nominative} {_norm(ctx.city)}", f"{nominative} {city_in}",
                f"{_norm(ctx.form_buy)} {city_in}"}
    return allowed


def validate_tags(tags: dict, ctx: MetaContext) -> list[str]:
    errors: list[str] = []
    title, h1, description = tags["title"], tags["h1"], tags["meta_description"]
    city_in = _norm(ctx.city_in)
    suffix = f" | {ctx.brand}"

    if len(title) > TITLE_MAX:
        errors.append(f"title длиннее {TITLE_MAX} символов ({len(title)})")
    if not title.endswith(suffix):
        errors.append(f"title должен оканчиваться на «{suffix}»")
    lead = ctx.form_nominative if ctx.chosen_form == "nominative" else ctx.form_buy
    if not _norm(title).startswith(_norm(lead)):
        errors.append(f"title должен начинаться с «{lead}»")
    if city_in not in _norm(title):
        errors.append(f"в title нет «{ctx.city_in}»")
    if "купит" not in _norm(title) and "цен" not in _norm(title):
        errors.append("в title нет продающего слова «купить» или «цена»")

    if not h1:
        errors.append("h1 пустой")
    if len(h1) > H1_MAX:
        errors.append(f"h1 длиннее {H1_MAX} символов ({len(h1)})")
    if _norm(ctx.brand) in _norm(h1):
        errors.append("в h1 не должно быть бренда")
    if city_in not in _norm(h1):
        errors.append(f"в h1 нет «{ctx.city_in}»")
    if _norm(h1) == _norm(title.removesuffix(suffix)):
        errors.append("h1 не должен повторять title")

    if not DESCRIPTION_MIN <= len(description) <= DESCRIPTION_MAX:
        errors.append(f"description должен быть {DESCRIPTION_MIN}–{DESCRIPTION_MAX} "
                      f"символов ({len(description)})")
    if city_in not in _norm(description):
        errors.append(f"в description нет «{ctx.city_in}»")
    site_description = _norm(ctx.site_description)
    for label, pattern in FORBIDDEN_CLAIMS.items():
        if re.search(pattern, _norm(description)) and not re.search(pattern, site_description):
            errors.append(f"в description утверждение «{label}», которого нет в описании сайта")

    keywords = split_phrases(tags["meta_keywords"])
    if not keywords:
        errors.append("keywords пустые")
    if len(keywords) > KEYWORDS_MAX:
        errors.append(f"keywords: больше {KEYWORDS_MAX} фраз ({len(keywords)})")
    allowed = allowed_keywords(ctx)
    unknown = [phrase for phrase in keywords if _norm(phrase) not in allowed]
    if unknown:
        errors.append("keywords не из статистики Wordstat: " + ", ".join(unknown))

    ai_keywords = split_phrases(tags["ai_keywords"])
    if not AI_KEYWORDS_MIN <= len(ai_keywords) <= AI_KEYWORDS_MAX:
        errors.append(f"ai_keywords: нужно {AI_KEYWORDS_MIN}–{AI_KEYWORDS_MAX} фраз "
                      f"({len(ai_keywords)})")

    for name in TAG_FIELDS:
        hits = [stop for stop in ctx.stoplist if stop in _norm(tags[name])]
        if hits:
            errors.append(f"в {name} слова из стоп-листа: {', '.join(hits)}")
    return errors
```

- [ ] **Step 4: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_validate.py`
Expected: PASS (15 passed).

- [ ] **Step 5: Мутационная проверка**

Временно закомментировать проверку `if city_in not in _norm(h1):` в `validate_tags` → `test_h1_rules` должен упасть. Вернуть строку, прогнать тесты снова — PASS.

- [ ] **Step 6: Commit**

```bash
git add execution/backend/app/category_meta/validate.py execution/backend/tests/test_category_meta_validate.py
git commit -m "feat: проверка метатегов — длины, город, бренд, keywords из Wordstat, стоп-лист"
```

---

### Task 8: `SiteClient` — категории, метатеги, sitemap

**Files:**
- Modify: `execution/backend/app/sites/client.py`
- Test: `execution/backend/tests/test_sites_client_catalog.py`

**Не трогать:** форму `_send(fn, ...)` и существующие методы — их тесты подменяют `app.sites.client.requests.get/.post/.patch`.

- [ ] **Step 1: Тест (красный)**

`tests/test_sites_client_catalog.py`:

```python
import json

import pytest

from app.sites.client import SiteAPIError, SiteClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", content: bytes = b""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload if payload is not None else {}
        self.text = text
        self.content = content

    def json(self):
        return self._payload


def test_list_catalog_categories_follows_pages(monkeypatch):
    calls = []
    pages = {1: {"results": [{"id": 1}], "next": "https://s.ru/api/v1/catalog-categories/?page=2"},
             2: {"results": [{"id": 2}], "next": None}}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(payload=pages[int(url.rsplit("=", 1)[1])])

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    assert SiteClient("https://s.ru/", "tok").list_catalog_categories() == [{"id": 1}, {"id": 2}]
    assert calls[0][0] == "https://s.ru/api/v1/catalog-categories/?page=1"
    assert calls[0][1]["headers"]["Authorization"] == "Token tok"
    assert calls[0][1]["timeout"] == 120


def test_list_metatags(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda url, **kw: FakeResponse(payload={"results": [{"id": 3, "url": "/"}],
                                                                "next": None}))
    assert SiteClient("https://s.ru", "t").list_metatags() == [{"id": 3, "url": "/"}]


def test_create_metatag_posts_url_and_fields(monkeypatch):
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return FakeResponse(payload={"id": 41, "url": "/catalog/x/"})

    monkeypatch.setattr("app.sites.client.requests.post", fake_post)
    body = SiteClient("https://s.ru", "t").create_metatag("/catalog/x/", {"title": "T", "h1": "H"})
    assert body["id"] == 41
    assert sent["url"] == "https://s.ru/api/v1/metatags/"
    assert sent["json"] == {"url": "/catalog/x/", "title": "T", "h1": "H"}


def test_create_metatag_without_id_is_error(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda url, **kw: FakeResponse(payload={"detail": "ok"}))
    with pytest.raises(SiteAPIError):
        SiteClient("https://s.ru", "t").create_metatag("/x/", {})


def test_update_metatag_patches_by_id(monkeypatch):
    sent = {}

    def fake_patch(url, **kwargs):
        sent.update(url=url, **kwargs)
        return FakeResponse(payload={"id": 41})

    monkeypatch.setattr("app.sites.client.requests.patch", fake_patch)
    SiteClient("https://s.ru", "t").update_metatag(41, {"title": "T"})
    assert sent["url"] == "https://s.ru/api/v1/metatags/41/"
    assert sent["json"] == {"title": "T"}


def test_update_metatag_http_error(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.patch",
                        lambda url, **kw: FakeResponse(400, text="bad"))
    with pytest.raises(SiteAPIError) as err:
        SiteClient("https://s.ru", "t").update_metatag(41, {})
    assert err.value.status_code == 400


URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://s.ru/catalog/listovye-materialy/</loc></url>
<url><loc>https://s.ru/catalog/category/listovye-materialy/fanera/</loc></url>
<url><loc>https://s.ru/catalog/stroitelnye-smesi/?tip-smesi=gruntovka</loc></url>
</urlset>"""


def test_sitemap_paths_skip_filter_pages_and_send_no_token(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(content=URLSET)

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    paths = SiteClient("https://s.ru", "secret").fetch_sitemap_paths()
    assert paths == {"/catalog/listovye-materialy/",
                     "/catalog/category/listovye-materialy/fanera/"}
    assert calls[0][0] == "https://s.ru/sitemap.xml"
    assert "Authorization" not in calls[0][1]["headers"]


def test_sitemap_index_is_followed(monkeypatch):
    index = b"""<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap><loc>https://s.ru/sitemap-catalog.xml</loc></sitemap></sitemapindex>"""
    responses = {"https://s.ru/sitemap.xml": index, "https://s.ru/sitemap-catalog.xml": URLSET}
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda url, **kw: FakeResponse(content=responses[url]))
    assert "/catalog/listovye-materialy/" in SiteClient("https://s.ru", "t").fetch_sitemap_paths()


def test_sitemap_not_xml(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda url, **kw: FakeResponse(content=b"<html>cookie"))
    with pytest.raises(SiteAPIError):
        SiteClient("https://s.ru", "t").fetch_sitemap_paths()
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_sites_client_catalog.py`
Expected: FAIL — `AttributeError: 'SiteClient' object has no attribute 'list_catalog_categories'`.

- [ ] **Step 3: Импорты и константы**

Заменить блок импортов стандартной библиотеки:

```python
import io
import mimetypes
import re
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree
```

После `ADDRESSES_SERVICES_PATH = "/api/v1/addresses-services/"`:

```python
CATALOG_CATEGORIES_PATH = "/api/v1/catalog-categories/"
METATAGS_PATH = "/api/v1/metatags/"
SITEMAP_PATH = "/sitemap.xml"

# Хостинг сайтов отдаёт cookie-заглушку запросам без браузерного User-Agent
# (см. обход в app/companies/logo.py) — sitemap публичный, токен ему не нужен.
SITEMAP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; k1-content-panel)"}
```

- [ ] **Step 4: Конструктор**

Заменить `__init__` класса `SiteClient`:

```python
    def __init__(self, base_url: str, token: str, timeout: int = 60,
                upload_timeout: int = 120, heavy_timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        # Тяжёлые списки: страница catalog-categories — ~155 КБ с seo_text внутри,
        # 2026-09-16 первое чтение вернуло обрезанное тело.
        self.heavy_timeout = heavy_timeout
```

- [ ] **Step 5: Методы**

Вставить перед строкой `    # --- файлы ---`:

```python
    # --- каталог и метатеги (directions/2026-09-16-category-meta-design.md) ---

    def _list_all(self, path: str, what: str) -> list[dict]:
        """Все страницы списка DRF: пагинация ?page=N до пустого next."""
        items, page_number = [], 1
        while True:
            response = self._send(
                requests.get, f"{self.base_url}{path}?page={page_number}", what,
                headers=self._headers, timeout=self.heavy_timeout)
            body = self._json(response, what)
            items += body.get("results", [])
            if not body.get("next"):
                return items
            page_number += 1

    def list_catalog_categories(self) -> list[dict]:
        return self._list_all(CATALOG_CATEGORIES_PATH, "список категорий каталога")

    def list_metatags(self) -> list[dict]:
        """Полный список: фильтр ?url= сайт игнорирует (проверено 2026-09-16)."""
        return self._list_all(METATAGS_PATH, "список метатегов")

    def create_metatag(self, url: str, fields: dict) -> dict:
        response = self._send(
            requests.post, f"{self.base_url}{METATAGS_PATH}", "создание метатега",
            json={"url": url, **fields},
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        body = self._json(response, "создание метатега")
        if body.get("id") is None:
            raise SiteAPIError(f"создание метатега: ответ без id: {body}")
        return body

    def update_metatag(self, metatag_id: int, fields: dict) -> dict:
        what = f"обновление метатега {metatag_id}"
        response = self._send(
            requests.patch, f"{self.base_url}{METATAGS_PATH}{metatag_id}/", what,
            json=fields, headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        return self._json(response, what)

    def fetch_sitemap_paths(self) -> set[str]:
        """Пути страниц из sitemap.xml; индекс sitemap разворачивается. Url с
        query (~860 фильтр-страниц на stroybaza-moscow.ru) отбрасываются."""
        paths: set[str] = set()
        pending, seen = [f"{self.base_url}{SITEMAP_PATH}"], set()
        while pending:
            url = pending.pop()
            if url in seen:
                continue
            seen.add(url)
            response = self._send(requests.get, url, "sitemap", headers=SITEMAP_HEADERS,
                                  timeout=self.heavy_timeout)
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError as exc:
                raise SiteAPIError(f"sitemap: ответ не XML: {exc}") from exc
            locs = [node.text.strip() for node in root.iter()
                    if node.tag.endswith("loc") and node.text]
            if root.tag.endswith("sitemapindex"):
                pending += locs
                continue
            for loc in locs:
                parts = urlsplit(loc)
                if not parts.query:
                    paths.add(parts.path)
        return paths

```

- [ ] **Step 6: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_sites_client_catalog.py tests/test_sites_client.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add execution/backend/app/sites/client.py execution/backend/tests/test_sites_client_catalog.py
git commit -m "feat: клиент сайта читает категории, sitemap и пишет метатеги"
```

---

### Task 9: Синхронизация дерева категорий

**Files:**
- Create: `execution/backend/app/category_meta/tree.py`
- Test: `execution/backend/tests/test_category_meta_tree.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_category_meta_tree.py`:

```python
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.category_meta.tree import (
    SKIP_DELETED, SKIP_NO_PAGE, SKIP_UNPUBLISHED, category_path, category_url, sync_categories,
)
from app.models.category_meta import CategoryMeta
from app.models.site import Site
from app.sites.client import SiteAPIError

CATEGORIES = [
    {"id": 45, "name": "Листовые материалы", "slug": "listovye-materialy", "parent": None,
     "published": True},
    {"id": 46, "name": "Фанера", "slug": "fanera", "parent": 45, "published": True},
    {"id": 60, "name": "Крепеж", "slug": "krepezh", "parent": None, "published": True},
    {"id": 61, "name": "Механические анкеры", "slug": "mehanicheskie-ankery", "parent": 60,
     "published": True},
    {"id": 62, "name": "Анкер клиновой", "slug": "anker-klinovoj", "parent": 61, "published": True},
    {"id": 70, "name": "Водосток Docke", "slug": "vodostok-docke", "parent": None,
     "published": False},
    {"id": 71, "name": "Сэндвич-панели", "slug": "sendvich-paneli", "parent": 60, "published": True},
]
SITEMAP = {"/catalog/listovye-materialy/", "/catalog/category/listovye-materialy/fanera/",
           "/catalog/krepezh/", "/catalog/category/krepezh/mehanicheskie-ankery/",
           "/catalog/category/mehanicheskie-ankery/anker-klinovoj/"}


@pytest.fixture
def site(db_session):
    row = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


def fake_client(categories=CATEGORIES, sitemap=SITEMAP):
    def fetch_sitemap_paths():
        if isinstance(sitemap, Exception):
            raise sitemap
        return sitemap
    return SimpleNamespace(list_catalog_categories=lambda: categories,
                           fetch_sitemap_paths=fetch_sitemap_paths)


def rows(db, site):
    return {r.remote_id: r for r in db.scalars(
        select(CategoryMeta).where(CategoryMeta.site_id == site.id)).all()}


def test_url_rule_root_and_nested():
    by_id = {c["id"]: c for c in CATEGORIES}
    assert category_url(by_id[45], by_id) == "/catalog/listovye-materialy/"
    assert category_url(by_id[46], by_id) == "/catalog/category/listovye-materialy/fanera/"
    # третий уровень — slug НЕПОСРЕДСТВЕННОГО родителя, а не корня
    assert category_url(by_id[62], by_id) == "/catalog/category/mehanicheskie-ankery/anker-klinovoj/"
    assert category_url({"id": 9, "slug": "x", "parent": 999}, by_id) == ""


def test_path_from_root():
    by_id = {c["id"]: c for c in CATEGORIES}
    assert category_path(by_id[62], by_id) == "Крепеж / Механические анкеры / Анкер клиновой"


def test_sync_creates_rows_and_skips(db_session, site):
    result = sync_categories(db_session, site, fake_client())
    by_remote = rows(db_session, site)
    assert {r.remote_id for r in result.active} == {45, 46, 60, 61, 62}
    assert result.skipped == 2 and result.sitemap_error == ""
    assert by_remote[46].status == "new"
    assert by_remote[46].url == "/catalog/category/listovye-materialy/fanera/"
    assert by_remote[46].remote_parent_id == 45
    assert (by_remote[70].status, by_remote[70].skip_reason) == ("skipped", SKIP_UNPUBLISHED)
    assert (by_remote[71].status, by_remote[71].skip_reason) == ("skipped", SKIP_NO_PAGE)


def test_sync_keeps_existing_status_and_tags(db_session, site):
    sync_categories(db_session, site, fake_client())
    fanera = rows(db_session, site)[46]
    fanera.status, fanera.title = "done", "Фанера в Москве | Стройбаза"
    db_session.commit()
    renamed = [dict(c, name="Фанера строительная") if c["id"] == 46 else c for c in CATEGORIES]
    sync_categories(db_session, site, fake_client(renamed))
    fanera = rows(db_session, site)[46]
    assert fanera.status == "done"
    assert fanera.title == "Фанера в Москве | Стройбаза"
    assert fanera.name == "Фанера строительная"


def test_sync_marks_deleted_and_revives(db_session, site):
    sync_categories(db_session, site, fake_client())
    without_fanera = [c for c in CATEGORIES if c["id"] != 46]
    sync_categories(db_session, site, fake_client(without_fanera))
    assert (rows(db_session, site)[46].status, rows(db_session, site)[46].skip_reason) == \
        ("skipped", SKIP_DELETED)
    sync_categories(db_session, site, fake_client())
    assert (rows(db_session, site)[46].status, rows(db_session, site)[46].skip_reason) == ("new", "")


def test_sitemap_failure_does_not_skip(db_session, site):
    result = sync_categories(db_session, site, fake_client(sitemap=SiteAPIError("sitemap: 503")))
    assert {r.remote_id for r in result.active} == {45, 46, 60, 61, 62, 71}
    assert result.sitemap_error == "sitemap: 503"
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_tree.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.category_meta.tree'`.

- [ ] **Step 3: Реализация — `app/category_meta/tree.py`**

```python
"""Синхронизация дерева категорий каталога сайта в category_meta.

Url категории — по правилу, сверенному 2026-09-16 со всеми 70 категориями из
sitemap stroybaza-moscow.ru: корневая — /catalog/{slug}/, вложенная —
/catalog/category/{slug непосредственного родителя}/{slug}/. Sitemap нужен
только чтобы отсеять категории без живой страницы; недоступен — не отсеиваем.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models.category_meta import CategoryMeta
from app.sites.client import SiteAPIError

SKIP_UNPUBLISHED = "не опубликована"
SKIP_NO_PAGE = "нет страницы на сайте"
SKIP_DELETED = "удалена на сайте"


@dataclass
class SyncResult:
    active: list[CategoryMeta]
    skipped: int
    sitemap_error: str


def category_url(category: dict, by_id: dict[int, dict]) -> str:
    parent_id = category.get("parent")
    if not parent_id:
        return f"/catalog/{category['slug']}/"
    parent = by_id.get(parent_id)
    if parent is None:
        return ""      # родитель не пришёл в выгрузке — url не угадываем
    return f"/catalog/category/{parent['slug']}/{category['slug']}/"


def category_path(category: dict, by_id: dict[int, dict]) -> str:
    names, current, seen = [], category, set()
    while current is not None and current["id"] not in seen:
        seen.add(current["id"])
        names.append(str(current.get("name", "")).strip())
        parent_id = current.get("parent")
        current = by_id.get(parent_id) if parent_id else None
    return " / ".join(reversed(names))


def sync_categories(db: Session, site, site_client) -> SyncResult:
    remote = site_client.list_catalog_categories()
    try:
        sitemap: set[str] | None = site_client.fetch_sitemap_paths()
        sitemap_error = ""
    except SiteAPIError as exc:
        sitemap, sitemap_error = None, str(exc)

    by_id = {int(item["id"]): item for item in remote}
    existing = {row.remote_id: row for row in db.scalars(
        select(CategoryMeta).where(CategoryMeta.site_id == site.id)).all()}
    active: list[CategoryMeta] = []
    skipped = 0
    for remote_id, data in by_id.items():
        row = existing.pop(remote_id, None)
        if row is None:
            row = CategoryMeta(site_id=site.id, remote_id=remote_id)
            db.add(row)
        row.remote_parent_id = data.get("parent") or None
        row.name = str(data.get("name", "")).strip()
        row.slug = data.get("slug") or ""
        row.path = category_path(data, by_id)
        row.url = category_url(data, by_id)
        if not data.get("published"):
            reason = SKIP_UNPUBLISHED
        elif not row.url or (sitemap is not None and row.url not in sitemap):
            reason = SKIP_NO_PAGE
        else:
            reason = ""
        row.skip_reason = reason
        if reason:
            row.status = "skipped"
            skipped += 1
        else:
            if row.status in (None, "skipped"):
                row.status = "new"
            active.append(row)
        row.updated_at = utcnow()
    for row in existing.values():
        row.skip_reason = SKIP_DELETED
        row.status = "skipped"
        row.updated_at = utcnow()
        skipped += 1
    db.commit()
    return SyncResult(active=active, skipped=skipped, sitemap_error=sitemap_error)
```

- [ ] **Step 4: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_tree.py`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/category_meta/tree.py execution/backend/tests/test_category_meta_tree.py
git commit -m "feat: синхронизация дерева категорий каталога с сайта"
```

---

### Task 10: Промпты, поисковые фразы, город с предлогом

**Files:**
- Modify: `execution/backend/app/ai/prompts.py`
- Modify: `execution/backend/app/seed.py`
- Create: `execution/backend/app/category_meta/seeds.py`, `app/category_meta/city.py`
- Modify: `execution/backend/tests/test_ai_prompts.py`, `tests/test_api_admin_prompts.py`
- Test: `execution/backend/tests/test_category_meta_prompts.py`, `tests/test_category_meta_seeds.py`, `tests/test_category_meta_city.py`

- [ ] **Step 1: Новые тесты (красные)**

`tests/test_category_meta_prompts.py`:

```python
from app.ai.prompts import render_prompt, resolve_prompt
from app.seed import seed_prompts

META_VARS = {"site_name": "Стройбаза", "site_description": "Магазин, доставка по Москве",
             "category_name": "Фанера", "category_path": "Листовые материалы / Фанера",
             "form_nominative": "фанера", "form_buy": "купить фанеру", "form_price": "цена фанеры",
             "chosen_form": "nominative", "sell_word": "купить", "city_in": "в Москве",
             "brand": "Стройбаза", "total_count": 96275,
             "phrases": ["фанера — 96275", "фанера купить — 12238"], "violations": []}


def render(db, **overrides):
    seed_prompts(db)
    return render_prompt(resolve_prompt(db, "category_meta", None), {**META_VARS, **overrides})


def test_meta_prompt_nominative_instruction(db_session):
    text = render(db_session)
    assert "начни title с «фанера»" in text
    assert "начни title с «купить фанеру»" not in text
    assert "фанера купить — 12238" in text
    assert "не прошёл проверку" not in text


def test_meta_prompt_declined_instruction(db_session):
    text = render(db_session, chosen_form="declined")
    assert "начни title с «купить фанеру»" in text


def test_meta_prompt_lists_violations(db_session):
    text = render(db_session, violations=["h1 пустой", "в title нет «в Москве»"])
    assert "Предыдущий вариант не прошёл проверку" in text
    assert "- в title нет «в Москве»" in text


def test_seeds_prompt_lists_categories(db_session):
    seed_prompts(db_session)
    text = render_prompt(resolve_prompt(db_session, "category_seeds", None),
                         {"site_name": "С", "site_description": "о",
                          "categories": ["46: Листовые материалы / Фанера", "62: Крепеж / Анкер"]})
    assert "46: Листовые материалы / Фанера\n62: Крепеж / Анкер" in text
```

`tests/test_category_meta_seeds.py`:

```python
from types import SimpleNamespace

import pytest

from app.ai.text import JsonResult
from app.category_meta.seeds import (
    SeedsError, apply_seeds, generate_seeds, needs_seed, seed_lines,
)
from app.models.category_meta import CategoryMeta
from app.models.site import Site
from app.seed import seed_prompts


def category(remote_id, name, path="", **kwargs):
    return CategoryMeta(remote_id=remote_id, name=name, path=path, **kwargs)


def test_needs_seed_for_new_and_renamed():
    assert needs_seed(category(1, "Фанера"))
    assert not needs_seed(category(1, "Фанера", seed_phrase="фанера", seed_source_name="Фанера"))
    assert needs_seed(category(1, "Фанера ФК", seed_phrase="фанера", seed_source_name="Фанера"))


def test_seed_lines_use_path():
    assert seed_lines([category(46, "Фанера", "Листовые материалы / Фанера"),
                       category(7, "Крепеж")]) == ["46: Листовые материалы / Фанера", "7: Крепеж"]


def test_apply_seeds_fills_forms_and_reports_missing():
    fanera, ugolok, broken = category(46, "Фанера"), category(50, "Уголок металлический"), category(51, "Х")
    data = [{"id": "46", "phrase": "фанера", "nominative": "фанера", "buy": "купить  фанеру",
             "price": "цена фанеры"},
            {"id": 51, "phrase": "х", "nominative": "", "buy": "купить х", "price": "цена х"},
            "мусор", {"id": "не число"}]
    missing = apply_seeds([fanera, ugolok, broken], data)
    assert missing == [ugolok, broken]
    assert (fanera.seed_phrase, fanera.form_buy, fanera.seed_source_name) == \
        ("фанера", "купить фанеру", "Фанера")
    assert not broken.seed_phrase


def test_apply_seeds_rejects_non_list():
    with pytest.raises(SeedsError):
        apply_seeds([category(1, "А")], {"id": 1})


def test_generate_seeds_chunks_and_records_usage(db_session, monkeypatch):
    monkeypatch.setattr("app.category_meta.seeds.SEEDS_CHUNK", 2)
    seed_prompts(db_session)
    site = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(site)
    db_session.commit()
    rows = [category(i, f"Категория {i}", site_id=site.id) for i in (1, 2, 3)]
    db_session.add_all(rows)
    db_session.commit()
    prompts, usage = [], []

    def complete_json(prompt):
        prompts.append(prompt)
        ids = [int(line.split(":")[0]) for line in prompt.splitlines()
               if line[:1].isdigit() and ": Категория" in line]
        return JsonResult([{"id": i, "phrase": f"ф{i}", "nominative": f"ф{i}",
                            "buy": f"купить ф{i}", "price": f"цена ф{i}"} for i in ids], 10, 5, 0.1)

    missing = generate_seeds(db_session, site, rows, SimpleNamespace(complete_json=complete_json),
                             lambda tp, tc, cost: usage.append((tp, tc, cost)))
    assert missing == []
    assert len(prompts) == 2 and len(usage) == 2
    assert [r.seed_phrase for r in rows] == ["ф1", "ф2", "ф3"]
```

`tests/test_category_meta_city.py`:

```python
from types import SimpleNamespace

import pytest

from app.ai.text import TextResult
from app.category_meta.city import CityFormError, city_in_for


def client(answer, prompts=None):
    def complete_text(prompt):
        if prompts is not None:
            prompts.append(prompt)
        return TextResult(answer, 1, 1, 0.0)
    return SimpleNamespace(complete_text=complete_text)


def test_city_in_normalizes_answer():
    prompts = []
    assert city_in_for(client(" «Во Владимире» \n", prompts), " Владимир ") == "во Владимире"
    assert "«Владимир»" in prompts[0]


@pytest.mark.parametrize("answer,expected", [
    # 2026-09-16 на живой проверке модель вернула всю фразу из примера промпта
    ("купить стройматериалы в Москве", "в Москве"),
    ("Ответ: в Великом Новгороде.", "в Великом Новгороде"),
    ("в Ростове-на-Дону", "в Ростове-на-Дону"),
])
def test_city_in_extracted_from_longer_answer(answer, expected):
    assert city_in_for(client(answer), "Город") == expected


def test_city_in_rejects_answer_without_preposition():
    with pytest.raises(CityFormError):
        city_in_for(client("Москве"), "Москва")
    with pytest.raises(CityFormError):
        city_in_for(client("не знаю такого города"), "Москва")
```

- [ ] **Step 2: Поправить существующие тесты промптов**

`tests/test_ai_prompts.py`, в словаре `contexts` теста `test_default_prompts_render_with_real_contexts` — после элемента `"builder_text": {...}` добавить:

```python
        "category_seeds": {"site_name": "X", "site_description": "описание",
                           "categories": ["46: Листовые материалы / Фанера"]},
        "category_meta": {"site_name": "X", "site_description": "описание",
                          "category_name": "Фанера", "category_path": "Листовые материалы / Фанера",
                          "form_nominative": "фанера", "form_buy": "купить фанеру",
                          "form_price": "цена фанеры", "chosen_form": "nominative",
                          "sell_word": "купить", "city_in": "в Москве", "brand": "Стройбаза",
                          "total_count": 96275, "phrases": ["фанера — 96275"],
                          "violations": ["h1 пустой"]},
```

`tests/test_api_admin_prompts.py` — счётчики промптов захардкожены и сломаются от любого нового ключа; перевести их на `PROMPT_KEYS`:

1. Импорт в начале файла: `from app.ai.prompts import PROMPT_KEYS` (перед `from app.ai.text import LLMError, TextResult`).
2. `assert keys == {"topics", "article_body", "cover", "content_image", "builder_text"}` → 
   ```python
   assert keys == {"topics", "article_body", "cover", "content_image", "builder_text",
                   "category_seeds", "category_meta"}
   ```
3. `assert len(admin_client.get("/api/admin/prompts").json()) == 6` →
   ```python
   # все глобальные дефолты + одно переопределение сайта
   assert len(admin_client.get("/api/admin/prompts").json()) == len(PROMPT_KEYS) + 1
   ```
4. `assert len(admin_client.get("/api/admin/prompts").json()) == 5` → `assert len(admin_client.get("/api/admin/prompts").json()) == len(PROMPT_KEYS)`
5. `assert sorted(keys) == ["article_body", "builder_text", "content_image", "cover", "topics"]` → `assert sorted(keys) == sorted(PROMPT_KEYS)`

- [ ] **Step 3: Убедиться, что падают**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_prompts.py tests/test_category_meta_seeds.py tests/test_category_meta_city.py tests/test_ai_prompts.py`
Expected: FAIL — нет промпта `category_meta`, нет модулей `seeds`/`city`, `KeyError: 'category_seeds'` в контекстах.

- [ ] **Step 4: `app/ai/prompts.py`**

```python
PROMPT_KEYS = ("topics", "article_body", "cover", "content_image", "builder_text",
               "category_seeds", "category_meta")
```

В `PROMPT_VARIABLES` после `"builder_text": frozenset({...}),`:

```python
    # Метатеги категорий: app/category_meta/seeds.py и app/category_meta/generator.py.
    "category_seeds": frozenset({"site_name", "site_description", "categories"}),
    "category_meta": frozenset({"site_name", "site_description", "category_name",
                                "category_path", "form_nominative", "form_buy", "form_price",
                                "chosen_form", "sell_word", "city_in", "brand", "total_count",
                                "phrases", "violations"}),
```

- [ ] **Step 5: Дефолтные промпты в `app/seed.py`**

В `DEFAULT_PROMPTS` после значения `"builder_text"` (перед закрывающей `}` словаря) добавить два ключа. Текст — дословно:

```python
    # Метатеги категорий (directions/2026-09-16-category-meta-design.md). Фраза
    # и формы — отдельным вызовом на всё дерево: по ним код ходит в Wordstat.
    "category_seeds": """Ты SEO-специалист интернет-магазина «{{ site_name }}».

О сайте:
{{ site_description }}

Ниже категории каталога: id и путь в дереве. Для каждой категории определи,
как её товары ищут в Яндексе, и верни поисковую фразу и её формы.

Правила:
- phrase — короткая фраза в том виде, в каком её вводят в поиск: обычный
  порядок слов («металлический уголок», а не «уголок металлический»), без
  перечислений из названия раздела (для «Кирпич Блоки Тротуар» выбери главный
  товар по смыслу пути); бренд оставляй, если он есть в названии категории;
- nominative — phrase в именительном падеже;
- buy — «купить» + phrase в винительном падеже: «купить фанеру», «купить
  металлический уголок»;
- price — «цена» + phrase в родительном падеже: «цена фанеры»;
- всё в нижнем регистре, кроме брендов латиницей.

Категории:
{% for line in categories %}{{ line }}
{% endfor %}
Верни СТРОГО JSON-массив объектов, по одному на каждую категорию, без пояснений:
[{"id": 46, "phrase": "фанера", "nominative": "фанера", "buy": "купить фанеру", "price": "цена фанеры"}]""",

    # Форму названия выбирает код по Wordstat (chosen_form) — модель её не меняет.
    # Правила полей дублируют проверку app/category_meta/validate.py: нарушение
    # всё равно вернётся модели списком violations на второй попытке.
    "category_meta": """Ты SEO-специалист интернет-магазина «{{ site_name }}». Напиши
метатеги страницы категории каталога.

О сайте (единственный источник фактов о магазине — доставка, оплата и т.п.):
{{ site_description }}

Категория: {{ category_name }} (путь: {{ category_path }})
Поисковая фраза: {{ form_nominative }}; «купить»: {{ form_buy }}; «цена»: {{ form_price }}
Город: {{ city_in }}. Бренд: {{ brand }}.
Запросов по фразе в городе за 30 дней: {{ total_count }}.

Как ищут (Wordstat, фраза — частота в месяц):
{% for line in phrases %}{{ line }}
{% endfor %}
Форма названия выбрана по статистике — используй её, не меняй:
{% if chosen_form == "nominative" %}- чаще ищут без склонения: начни title с «{{ form_nominative }}» (с заглавной буквы) и построй фразу так, чтобы она читалась корректно, например «{{ form_nominative }} {{ city_in }} — купить по выгодной цене | {{ brand }}».
{% else %}- чаще ищут со склонением: начни title с «{{ form_buy }}» (с заглавной буквы), например «{{ form_buy }} {{ city_in }} по выгодной цене | {{ brand }}».
{% endif %}Продающее слово, которое чаще встречается в запросах: «{{ sell_word }}».

Требования:
- title: до 70 символов, оканчивается на « | {{ brand }}», содержит «{{ city_in }}» и слово «купить» или «цена»;
- h1: до 60 символов, фраза и город («{{ form_nominative }} {{ city_in }}», с заглавной буквы), без бренда, не повторяет title;
- meta_description: 140–170 символов, содержит «{{ city_in }}» и фразу; продающие слова из статистики и виды товара из запросов; не пиши «в наличии», «скидки», «оптом» и другие факты о магазине, которых нет в описании сайта;
- meta_keywords: до 10 фраз через запятую в нижнем регистре, ТОЛЬКО дословно из списка запросов выше или «{{ form_nominative }} {{ city_in }}»; сначала коммерческие (купить, цена), затем виды товара;
- ai_keywords: 10–15 фраз через запятую — как человек спросил бы нейросеть о выборе и покупке этого товара: «где купить … {{ city_in }}», «какая … подходит для …», виды и назначения из запросов.
{% if violations %}
Предыдущий вариант не прошёл проверку. Исправь:
{% for item in violations %}- {{ item }}
{% endfor %}{% endif %}
Верни СТРОГО JSON-объект без пояснений:
{"title": "...", "h1": "...", "meta_description": "...", "meta_keywords": "...", "ai_keywords": "..."}""",
```

- [ ] **Step 6: `app/category_meta/seeds.py`**

```python
"""Поисковая фраза и формы для категорий — один вызов LLM на пачку категорий.

Фраза перезапрашивается только у новых и переименованных категорий
(seed_source_name != name): платить за неизменившееся дерево на каждом
запуске незачем."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.prompts import render_prompt, resolve_prompt
from app.models.category_meta import CategoryMeta

# Сколько категорий в одном запросе: 77 категорий stroybaza-moscow.ru — два
# вызова; ответ на 60 объектов укладывается в лимит вывода модели с запасом.
SEEDS_CHUNK = 60
SEED_FIELDS = ("phrase", "nominative", "buy", "price")


class SeedsError(RuntimeError):
    pass


def needs_seed(row: CategoryMeta) -> bool:
    return not row.seed_phrase or row.seed_source_name != row.name


def seed_lines(rows: list[CategoryMeta]) -> list[str]:
    return [f"{row.remote_id}: {row.path or row.name}" for row in rows]


def apply_seeds(rows: list[CategoryMeta], data: object) -> list[CategoryMeta]:
    """Раскладывает ответ модели по категориям. Возвращает те, для которых
    фраза не пришла или пришла неполной."""
    if not isinstance(data, list):
        raise SeedsError("модель вернула не JSON-массив поисковых фраз")
    by_id: dict[int, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            by_id[int(item.get("id"))] = item
        except (TypeError, ValueError):
            continue
    missing = []
    for row in rows:
        item = by_id.get(row.remote_id) or {}
        values = [" ".join(str(item.get(name) or "").split()) for name in SEED_FIELDS]
        if not all(values):
            missing.append(row)
            continue
        row.seed_phrase, row.form_nominative, row.form_buy, row.form_price = values
        row.seed_source_name = row.name
    return missing


def generate_seeds(db: Session, site, rows: list[CategoryMeta], text_client,
                   record_usage) -> list[CategoryMeta]:
    template = resolve_prompt(db, "category_seeds", site.id)
    missing: list[CategoryMeta] = []
    for start in range(0, len(rows), SEEDS_CHUNK):
        chunk = rows[start:start + SEEDS_CHUNK]
        prompt = render_prompt(template, {"site_name": site.name,
                                          "site_description": site.site_description,
                                          "categories": seed_lines(chunk)})
        result = text_client.complete_json(prompt)
        record_usage(result.tokens_prompt, result.tokens_completion, result.cost)
        missing += apply_seeds(chunk, result.data)
    db.commit()
    return missing
```

- [ ] **Step 7: `app/category_meta/city.py`**

```python
"""Город с предлогом для тегов: «в Москве», «во Владимире».

Промпт не выносится в редактируемые: это грамматика, а не редакционная
политика, и ошибка здесь видна сразу — менеджер правит поле руками."""

from __future__ import annotations

import re

CITY_IN_PROMPT = """Поставь название города «{city}» в предложный падеж с правильным
предлогом «в» или «во». Примеры: Москва → в Москве; Владимир → во Владимире;
Тверь → в Твери; Великий Новгород → в Великом Новгороде.
Верни ТОЛЬКО предлог и название города — например «в Москве», без других слов,
кавычек и пояснений."""

# Предлог и название из заглавных слов в КОНЦЕ ответа: 2026-09-16 модель вернула
# «купить стройматериалы в Москве» — лишние слова перед городом отбрасываем.
_CITY_IN = re.compile(r"(?:^|\s)(во?\s+[А-ЯЁA-Z][\w-]*(?:\s+[А-ЯЁA-Z][\w-]*)*)$", re.IGNORECASE)
_CAPITAL = re.compile(r"^[А-ЯЁA-Z]")


class CityFormError(RuntimeError):
    pass


def city_in_for(text_client, city: str) -> str:
    result = text_client.complete_text(CITY_IN_PROMPT.format(city=city.strip()))
    value = " ".join(result.text.strip().strip("\"'«».").split()).rstrip(".!")
    match = _CITY_IN.search(value)
    if match is None:
        raise CityFormError(f"модель вернула неожиданную форму города: {value[:100]!r}")
    phrase = match.group(1)
    preposition, _, name = phrase.partition(" ")
    # IGNORECASE нужен для «Во», но название города обязано быть с заглавной —
    # иначе «не знаю такого города» сошло бы за «в» + слово.
    if not _CAPITAL.match(name):
        raise CityFormError(f"модель вернула неожиданную форму города: {value[:100]!r}")
    return f"{preposition.lower()} {name}"
```

- [ ] **Step 8: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_prompts.py tests/test_category_meta_seeds.py tests/test_category_meta_city.py tests/test_ai_prompts.py tests/test_api_admin_prompts.py`
Expected: PASS (60 passed).

- [ ] **Step 9: Commit**

```bash
git add execution/backend/app/ai/prompts.py execution/backend/app/seed.py execution/backend/app/category_meta/seeds.py execution/backend/app/category_meta/city.py execution/backend/tests/
git commit -m "feat: промпты метатегов, поисковые фразы категорий и город с предлогом"
```

---

### Task 11: Запись метатега на сайт

**Files:**
- Create: `execution/backend/app/category_meta/publish.py`
- Test: `execution/backend/tests/test_category_meta_publish.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_category_meta_publish.py`:

```python
import pytest

from app.category_meta.publish import publish_metatag
from app.models.category_meta import CategoryMeta
from app.sites.client import SiteAPIError

TAGS = {"title": "Фанера в Москве — купить | Стройбаза", "h1": "Фанера в Москве",
        "meta_description": "описание", "meta_keywords": "фанера", "ai_keywords": "где купить"}
URL = "/catalog/category/listovye-materialy/fanera/"


class FakeSite:
    def __init__(self, metatags=None, create_errors=None, update_errors=None):
        self.metatags = list(metatags or [])
        self.create_errors = list(create_errors or [])
        self.update_errors = list(update_errors or [])
        self.created, self.updated = [], []

    def list_metatags(self):
        return [dict(item) for item in self.metatags]

    def create_metatag(self, url, fields):
        item = {"id": 100 + len(self.metatags), "url": url, **fields}
        self.metatags.append(item)      # запись появляется, даже если ответ «не дошёл»
        self.created.append((url, fields))
        if self.create_errors:
            raise self.create_errors.pop(0)
        return item

    def update_metatag(self, metatag_id, fields):
        if self.update_errors:
            raise self.update_errors.pop(0)
        self.updated.append((metatag_id, fields))
        return {"id": metatag_id}


def category():
    return CategoryMeta(remote_id=46, name="Фанера", url=URL)


def test_existing_metatag_is_patched_and_previous_saved():
    site = FakeSite([{"id": 39, "url": URL, "title": "", "h1": "Фанера любых видов",
                      "meta_description": None, "meta_keywords": "", "ai_keywords": "",
                      "seo_text": "<p>текст</p>"}])
    row = category()
    publish_metatag(site, row, TAGS, sleep=lambda s: None)
    assert site.updated == [(39, TAGS)]
    assert site.created == []
    assert row.remote_metatag_id == 39
    assert row.previous_json == {"title": "", "h1": "Фанера любых видов", "meta_description": "",
                                 "meta_keywords": "", "ai_keywords": ""}


def test_missing_metatag_is_created():
    site = FakeSite([{"id": 1, "url": "/"}])
    row = category()
    publish_metatag(site, row, TAGS, sleep=lambda s: None)
    assert site.created == [(URL, TAGS)]
    assert row.previous_json == {}
    assert row.remote_metatag_id == 101


def test_previous_json_is_not_overwritten_by_our_own_values():
    site = FakeSite([{"id": 39, "url": URL, **TAGS}])
    row = category()
    row.previous_json = {"title": "", "h1": "Фанера любых видов", "meta_description": "",
                         "meta_keywords": "", "ai_keywords": ""}
    publish_metatag(site, row, TAGS, sleep=lambda s: None)
    assert row.previous_json["h1"] == "Фанера любых видов"


def test_lost_create_response_does_not_duplicate():
    site = FakeSite(create_errors=[SiteAPIError("создание метатега: сеть недоступна")])
    row = category()
    publish_metatag(site, row, TAGS, sleep=lambda s: None)
    assert len(site.created) == 1
    assert [item["url"] for item in site.metatags] == [URL]
    assert site.updated == [(100, TAGS)]


def test_client_error_is_not_retried():
    site = FakeSite([{"id": 39, "url": URL}],
                    update_errors=[SiteAPIError("HTTP 400", status_code=400)])
    with pytest.raises(SiteAPIError):
        publish_metatag(site, category(), TAGS, sleep=lambda s: None)
    assert site.updated == []


def test_server_error_retried_then_raised():
    sleeps = []
    site = FakeSite([{"id": 39, "url": URL}],
                    update_errors=[SiteAPIError("HTTP 502", status_code=502)] * 3)
    with pytest.raises(SiteAPIError):
        publish_metatag(site, category(), TAGS, sleep=sleeps.append)
    assert sleeps == [1, 2]
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_publish.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.category_meta.publish'`.

- [ ] **Step 3: Реализация — `app/category_meta/publish.py`**

```python
"""Запись тегов категории в /api/v1/metatags/ сайта.

Есть метатег с этим url — PATCH пяти полей, нет — POST. seo_text не трогаем.
POST вслепую не повторяется: ReadTimeout после отправки неотличим от «создано,
ответ не дошёл». Поэтому каждая попытка заново читает список метатегов — если
предыдущий POST на деле прошёл, следующая попытка найдёт запись и сделает PATCH
(тот же приём, что _guard_duplicate_url у статей)."""

from __future__ import annotations

import time

from app.models.category_meta import CategoryMeta
from app.sites.client import SiteAPIError

PUBLISHED_FIELDS = ("title", "h1", "meta_description", "meta_keywords", "ai_keywords")
MAX_ATTEMPTS = 3


def _retryable(exc: SiteAPIError) -> bool:
    return exc.status_code is None or exc.status_code >= 500


def _find(site_client, url: str) -> dict | None:
    for item in site_client.list_metatags():
        if item.get("url") == url:
            return item
    return None


def publish_metatag(site_client, category: CategoryMeta, tags: dict, sleep=time.sleep) -> None:
    fields = {name: tags[name] for name in PUBLISHED_FIELDS}
    for attempt in range(MAX_ATTEMPTS):
        try:
            existing = _find(site_client, category.url)
            if existing is not None:
                if category.previous_json is None:
                    category.previous_json = {name: existing.get(name) or ""
                                              for name in PUBLISHED_FIELDS}
                site_client.update_metatag(existing["id"], fields)
                category.remote_metatag_id = existing["id"]
            else:
                if category.previous_json is None:
                    category.previous_json = {}
                created = site_client.create_metatag(category.url, fields)
                category.remote_metatag_id = created["id"]
            return
        except SiteAPIError as exc:
            if not _retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            sleep(2 ** attempt)
```

- [ ] **Step 4: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_publish.py`
Expected: PASS (6 passed).

- [ ] **Step 5: Мутационная проверка**

Временно убрать `_find(...)` из цикла (вызывать один раз до `for`) → `test_lost_create_response_does_not_duplicate` должен упасть. Вернуть.

- [ ] **Step 6: Commit**

```bash
git add execution/backend/app/category_meta/publish.py execution/backend/tests/test_category_meta_publish.py
git commit -m "feat: запись метатега категории на сайт без дублей при обрыве ответа"
```

---

### Task 12: Генерация тегов одной категории

**Files:**
- Create: `execution/backend/app/category_meta/generator.py`
- Test: `execution/backend/tests/test_category_meta_generator.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_category_meta_generator.py`:

```python
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.ai.text import JsonResult
from app.category_meta.generator import MetaValidationError, generate_category
from app.category_meta.seeds import SeedsError
from app.models.category_meta import CategoryMeta, WordstatCall
from app.models.site import Site
from app.seed import seed_prompts
from app.wordstat.cache import cache_put
from app.wordstat.quota import QuotaExceeded

TOP = {"totalCount": "96275", "results": [
    {"phrase": "фанера", "count": "96275"}, {"phrase": "фанера купить", "count": "12238"},
    {"phrase": "фанера цена", "count": "5300"}, {"phrase": "фанера москва", "count": "2338"},
    {"phrase": "купить фанеру в москве", "count": "1552"},
    {"phrase": "лист фанеры цена", "count": "2758"},
    {"phrase": "фанера влагостойкая", "count": "6325"},
    {"phrase": "ламинированная фанера", "count": "8428"},
    {"phrase": "фанера фсф", "count": "3058"}, {"phrase": "фанера фк", "count": "2495"},
    {"phrase": "леруа фанера", "count": "1206"}]}
EXACT = {'"!купить !фанера"': {"totalCount": "678"}, '"!купить !фанеру"': {"totalCount": "234"}}

VALID = {
    "title": "Фанера в Москве — купить по выгодной цене | Стройбаза",
    "h1": "Фанера в Москве",
    "meta_description": ("Фанера в Москве по выгодной цене: влагостойкая ФСФ, ФК, ламинированная и "
                         "берёзовая. Листы 1525×1525 и 2440×1220 мм, толщина 4–40 мм. "
                         "Доставка по Москве."),
    "meta_keywords": ("фанера, фанера купить, фанера цена, фанера москва, купить фанеру в москве, "
                      "лист фанеры цена, фанера влагостойкая, ламинированная фанера, фанера фсф, "
                      "фанера фк"),
    "ai_keywords": ("где купить фанеру в Москве, какая фанера подходит для пола, "
                    "влагостойкая фанера ФСФ цена, фанера ФК или ФСФ что выбрать, "
                    "ламинированная фанера для опалубки, фанера 18 мм купить в Москве, "
                    "лист фанеры 1525×1525 цена, берёзовая фанера для мебели, "
                    "фанера с доставкой по Москве, сколько стоит лист фанеры"),
}


class FakeWordstat:
    def __init__(self):
        self.calls = []

    def top_requests(self, phrase, region_id, num_phrases=300):
        self.calls.append((phrase, region_id, num_phrases))
        return EXACT.get(phrase) or (TOP if phrase == "фанера" else {"totalCount": "3"})


class FakeText:
    model = "m"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return JsonResult(self.answers.pop(0), 100, 50, 0.3)


class FakeSite:
    def __init__(self):
        self.created = []

    def list_metatags(self):
        return []

    def create_metatag(self, url, fields):
        self.created.append((url, fields))
        return {"id": 7}


@pytest.fixture
def site(db_session):
    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               site_description="Интернет-магазин стройматериалов, доставка по Москве",
               city="Москва", city_in="в Москве", wordstat_region_id=213, brand="Стройбаза")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def fanera(db_session, site):
    row = CategoryMeta(site_id=site.id, remote_id=46, name="Фанера",
                       path="Листовые материалы / Фанера",
                       url="/catalog/category/listovye-materialy/fanera/", status="in_work",
                       seed_phrase="фанера", seed_source_name="Фанера", form_nominative="фанера",
                       form_buy="купить фанеру", form_price="цена фанеры")
    db_session.add(row)
    db_session.commit()
    return row


def run(db, category, site, *, wordstat=None, text=None, site_client=None, limit=100,
        usage=None):
    wordstat = wordstat or FakeWordstat()
    generate_category(db, category, site, wordstat_factory=lambda: wordstat,
                      text_client=text or FakeText([VALID]), site_client=site_client or FakeSite(),
                      limit=limit, stoplist=["леруа"],
                      record_usage=lambda *args: (usage.append(args) if usage is not None else None))
    return wordstat


def test_full_cycle_with_form_check(db_session, site, fanera):
    site_client, usage = FakeSite(), []
    wordstat = run(db_session, fanera, site, site_client=site_client, usage=usage)
    assert wordstat.calls == [("фанера", 213, 300), ('"!купить !фанера"', 213, 1),
                              ('"!купить !фанеру"', 213, 1)]
    assert (fanera.chosen_form, fanera.nominative_count, fanera.declined_count) == \
        ("nominative", 678, 234)
    assert fanera.total_count == 96275 and fanera.low_demand is False
    assert fanera.status == "done" and fanera.title == VALID["title"]
    assert site_client.created[0][0] == "/catalog/category/listovye-materialy/fanera/"
    assert usage == [(100, 50, 0.3)]
    assert db_session.scalar(select(func.count()).select_from(WordstatCall)) == 3


def test_same_forms_need_one_query(db_session, site, fanera):
    fanera.seed_phrase = fanera.form_nominative = "профнастил"
    fanera.form_buy = "купить профнастил"
    answer = {**VALID, "title": "Купить профнастил в Москве по выгодной цене | Стройбаза",
              "h1": "Профнастил в Москве", "meta_keywords": "профнастил москва",
              "meta_description": VALID["meta_description"].replace("Фанера", "Профнастил")}
    wordstat = run(db_session, fanera, site, text=FakeText([answer]))
    assert wordstat.calls == [("профнастил", 213, 300)]
    assert fanera.chosen_form == "declined" and fanera.nominative_count is None
    assert fanera.low_demand is True


def test_cache_hit_skips_wordstat_and_quota(db_session, site, fanera):
    cache_put(db_session, "top", "фанера", 213, TOP)
    for phrase, body in EXACT.items():
        cache_put(db_session, "exact", phrase, 213, body)

    def no_client():
        raise AssertionError("всё в кеше — клиент Wordstat не нужен")

    generate_category(db_session, fanera, site, wordstat_factory=no_client,
                      text_client=FakeText([VALID]), site_client=FakeSite(), limit=100,
                      stoplist=[], record_usage=lambda *a: None)
    assert fanera.status == "done"
    assert db_session.scalar(select(func.count()).select_from(WordstatCall)) == 0


def test_quota_exhausted_stops_before_llm(db_session, site, fanera):
    db_session.add_all([WordstatCall() for _ in range(99)])
    db_session.commit()
    wordstat, text = FakeWordstat(), FakeText([VALID])
    with pytest.raises(QuotaExceeded) as err:
        run(db_session, fanera, site, wordstat=wordstat, text=text)
    assert err.value.seconds > 0
    assert wordstat.calls == [] and text.prompts == []


def test_stoplist_phrases_do_not_reach_prompt(db_session, site, fanera):
    text = FakeText([VALID])
    run(db_session, fanera, site, text=text)
    assert "фанера купить — 12238" in text.prompts[0]
    assert "леруа" not in text.prompts[0]


def test_invalid_answer_retried_with_violations(db_session, site, fanera):
    text = FakeText([{**VALID, "h1": ""}, VALID])
    run(db_session, fanera, site, text=text)
    assert len(text.prompts) == 2
    assert "- h1 пустой" in text.prompts[1]
    assert fanera.status == "done"


def test_invalid_twice_raises_and_publishes_nothing(db_session, site, fanera):
    site_client = FakeSite()
    with pytest.raises(MetaValidationError) as err:
        run(db_session, fanera, site, text=FakeText([{**VALID, "h1": ""}, ["не объект"]]),
            site_client=site_client)
    assert "модель вернула не JSON-объект" in str(err.value)
    assert site_client.created == []
    # факты Wordstat сохранены до LLM — их видно в карточке
    assert fanera.chosen_form == "nominative" and fanera.total_count == 96275


def test_category_without_seed(db_session, site, fanera):
    fanera.seed_phrase = ""
    with pytest.raises(SeedsError):
        run(db_session, fanera, site)
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_generator.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.category_meta.generator'`.

- [ ] **Step 3: Реализация — `app/category_meta/generator.py`**

```python
"""Метатеги одной категории: Wordstat → выбор формы → LLM → проверка → сайт.

Порядок шагов важен для денег и квоты:
- клиент Wordstat создаётся лениво и ДО резервирования квоты — без ключа квота
  не тратится;
- статистика берётся из кеша, в Wordstat уходят только промахи;
- факты Wordstat сохраняются в категорию до вызова LLM — если теги не пройдут
  проверку, в карточке всё равно видно, что показал Wordstat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.ai.prompts import render_prompt, resolve_prompt
from app.category_meta.forms import (
    LOW_DEMAND_THRESHOLD, buy_queries, choose_form, filter_stoplist, forms_differ, sell_word,
)
from app.category_meta.publish import publish_metatag
from app.category_meta.seeds import SeedsError
from app.category_meta.validate import (
    TAG_FIELDS, MetaContext, normalize_tags, validate_tags,
)
from app.clock import utcnow
from app.models.category_meta import CategoryMeta
from app.wordstat.cache import cache_get, cache_put
from app.wordstat.client import parse_top
from app.wordstat.quota import QuotaExceeded, reserve

TOP_KIND = "top"
EXACT_KIND = "exact"
TOP_PHRASES = 300        # сколько фраз просить у topRequests
PROMPT_PHRASES = 100     # сколько из них показать модели
LLM_ATTEMPTS = 2         # первая попытка + одна с перечнем нарушений


class MetaValidationError(RuntimeError):
    pass


@dataclass
class WordstatFacts:
    total_count: int
    phrases: list[tuple[str, int]]
    chosen_form: str
    nominative_count: int | None
    declined_count: int | None


def wordstat_queries(category: CategoryMeta) -> list[tuple[str, str]]:
    queries = [(TOP_KIND, category.seed_phrase)]
    if forms_differ(category.form_nominative, category.form_buy):
        nominative, declined = buy_queries(category.form_nominative, category.form_buy)
        queries += [(EXACT_KIND, nominative), (EXACT_KIND, declined)]
    return queries


def collect_facts(db: Session, category: CategoryMeta, region_id: int | None,
                  wordstat_factory, limit: int, now: datetime | None = None) -> WordstatFacts:
    queries = wordstat_queries(category)
    bodies = {query: cache_get(db, query[0], query[1], region_id, now) for query in queries}
    missing = [query for query, body in bodies.items() if body is None]
    if missing:
        client = wordstat_factory()
        wait = reserve(db, len(missing), limit, now)
        if wait:
            raise QuotaExceeded(wait)
        for kind, phrase in missing:
            # У точной формы нужен только totalCount — список фраз не просим.
            body = client.top_requests(phrase, region_id, TOP_PHRASES if kind == TOP_KIND else 1)
            cache_put(db, kind, phrase, region_id, body, now)
            bodies[(kind, phrase)] = body
    top = parse_top(bodies[queries[0]])
    if len(queries) == 3:
        nominative_count = parse_top(bodies[queries[1]]).total_count
        declined_count = parse_top(bodies[queries[2]]).total_count
        chosen = choose_form(nominative_count, declined_count)
    else:
        nominative_count = declined_count = None
        chosen = choose_form(None, None)
    return WordstatFacts(top.total_count, top.phrases, chosen, nominative_count, declined_count)


def generate_tags(db: Session, category: CategoryMeta, site, facts: WordstatFacts,
                  text_client, stoplist: list[str], record_usage) -> dict:
    phrases = filter_stoplist(facts.phrases, stoplist)
    ctx = MetaContext(form_nominative=category.form_nominative, form_buy=category.form_buy,
                      chosen_form=facts.chosen_form, city=site.city, city_in=site.city_in,
                      brand=site.brand, wordstat_phrases=[phrase for phrase, _ in phrases],
                      stoplist=stoplist, site_description=site.site_description)
    template = resolve_prompt(db, "category_meta", site.id)
    violations: list[str] = []
    for _attempt in range(LLM_ATTEMPTS):
        prompt = render_prompt(template, {
            "site_name": site.name, "site_description": site.site_description,
            "category_name": category.name, "category_path": category.path or category.name,
            "form_nominative": category.form_nominative, "form_buy": category.form_buy,
            "form_price": category.form_price, "chosen_form": facts.chosen_form,
            "sell_word": sell_word(phrases), "city_in": site.city_in, "brand": site.brand,
            "total_count": facts.total_count,
            "phrases": [f"{phrase} — {count}" for phrase, count in phrases[:PROMPT_PHRASES]],
            "violations": violations,
        })
        result = text_client.complete_json(prompt)
        record_usage(result.tokens_prompt, result.tokens_completion, result.cost)
        try:
            tags = normalize_tags(result.data)
        except ValueError as exc:
            violations = [str(exc)]
            continue
        violations = validate_tags(tags, ctx)
        if not violations:
            return tags
    raise MetaValidationError("теги не прошли проверку: " + "; ".join(violations))


def generate_category(db: Session, category: CategoryMeta, site, *, wordstat_factory,
                      text_client, site_client, limit: int, stoplist: list[str],
                      record_usage, now: datetime | None = None) -> None:
    if not category.seed_phrase:
        raise SeedsError("у категории нет поисковой фразы — обновите метатеги проекта целиком")
    facts = collect_facts(db, category, site.wordstat_region_id, wordstat_factory, limit, now)
    category.chosen_form = facts.chosen_form
    category.nominative_count = facts.nominative_count
    category.declined_count = facts.declined_count
    category.total_count = facts.total_count
    category.low_demand = facts.total_count < LOW_DEMAND_THRESHOLD
    db.commit()

    tags = generate_tags(db, category, site, facts, text_client, stoplist, record_usage)
    publish_metatag(site_client, category, tags)
    for name in TAG_FIELDS:
        setattr(category, name, tags[name])
    category.status = "done"
    category.error_text = ""
    category.wait_until = None
    category.updated_at = utcnow()
    db.commit()
```

- [ ] **Step 4: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_generator.py`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/category_meta/generator.py execution/backend/tests/test_category_meta_generator.py
git commit -m "feat: генерация метатегов категории — Wordstat, форма, LLM, проверка, сайт"
```

---

### Task 13: Состояние запуска

**Files:**
- Create: `execution/backend/app/category_meta/runs.py`
- Test: `execution/backend/tests/test_category_meta_runs.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_category_meta_runs.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from app.category_meta.runs import (
    active_run, finish_run_if_complete, is_stale, is_stuck, last_successful_run, next_queued,
    run_progress,
)
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.site import Site

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def site(db_session):
    row = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


def add_category(db, site, remote_id, status, *, minutes_ago=0, started_minutes_ago=None,
                 wait_in_minutes=None):
    row = CategoryMeta(site_id=site.id, remote_id=remote_id, name=f"К{remote_id}", status=status,
                       updated_at=NOW - timedelta(minutes=minutes_ago))
    if started_minutes_ago is not None:
        row.started_at = NOW - timedelta(minutes=started_minutes_ago)
    if wait_in_minutes is not None:
        row.wait_until = NOW + timedelta(minutes=wait_in_minutes)
    db.add(row)
    db.commit()
    return row


def add_run(db, site, minutes_ago=0, **kwargs):
    run = MetaRun(site_id=site.id, started_at=NOW - timedelta(minutes=minutes_ago), **kwargs)
    db.add(run)
    db.commit()
    return run


def test_is_stuck_after_30_minutes(db_session, site):
    assert not is_stuck(add_category(db_session, site, 1, "in_work", started_minutes_ago=29), NOW)
    assert is_stuck(add_category(db_session, site, 2, "in_work", started_minutes_ago=31), NOW)
    assert not is_stuck(add_category(db_session, site, 3, "done", started_minutes_ago=90), NOW)


def test_next_queued_in_id_order(db_session, site):
    add_category(db_session, site, 1, "done")
    second = add_category(db_session, site, 2, "queued")
    add_category(db_session, site, 3, "queued")
    assert next_queued(db_session, site.id).id == second.id


def test_fresh_run_is_not_stale(db_session, site):
    run = add_run(db_session, site, minutes_ago=5)
    add_category(db_session, site, 1, "queued", minutes_ago=5)
    assert not is_stale(db_session, run, NOW)


def test_run_without_signs_of_life_is_stale(db_session, site):
    run = add_run(db_session, site, minutes_ago=180)
    add_category(db_session, site, 1, "queued", minutes_ago=150)
    add_category(db_session, site, 2, "in_work", minutes_ago=150, started_minutes_ago=150)
    assert is_stale(db_session, run, NOW)


def test_waiting_for_quota_is_not_stale(db_session, site):
    run = add_run(db_session, site, minutes_ago=300)
    add_category(db_session, site, 1, "queued", minutes_ago=200, wait_in_minutes=20)
    assert not is_stale(db_session, run, NOW)


def test_working_category_is_not_stale(db_session, site):
    run = add_run(db_session, site, minutes_ago=300)
    add_category(db_session, site, 1, "in_work", minutes_ago=200, started_minutes_ago=3)
    assert not is_stale(db_session, run, NOW)


def test_progress_counts_and_nearest_wait(db_session, site):
    run = add_run(db_session, site, total=4)
    add_category(db_session, site, 1, "done")
    add_category(db_session, site, 2, "failed")
    add_category(db_session, site, 3, "queued", wait_in_minutes=40)
    add_category(db_session, site, 4, "queued", wait_in_minutes=10)
    add_category(db_session, site, 5, "skipped")
    progress = run_progress(db_session, run, NOW)
    assert (progress.total, progress.done, progress.failed) == (4, 1, 1)
    assert progress.wait_until.replace(tzinfo=timezone.utc) == NOW + timedelta(minutes=10)
    assert progress.stale is False


def test_finish_run_only_when_nothing_active(db_session, site):
    run = add_run(db_session, site)
    queued = add_category(db_session, site, 1, "queued")
    assert finish_run_if_complete(db_session, site.id, NOW) is None
    queued.status = "done"
    db_session.commit()
    assert finish_run_if_complete(db_session, site.id, NOW).id == run.id
    assert active_run(db_session, site.id) is None


def test_last_successful_run_ignores_failed(db_session, site):
    ok = add_run(db_session, site, minutes_ago=60, finished_at=NOW - timedelta(minutes=30))
    add_run(db_session, site, minutes_ago=20, finished_at=NOW - timedelta(minutes=10),
            error_text="сайт недоступен")
    assert last_successful_run(db_session, site.id).id == ok.id
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_runs.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.category_meta.runs'`.

- [ ] **Step 3: Реализация — `app/category_meta/runs.py`**

```python
"""Состояние запуска «Обновить метатеги»: прогресс, зависшие категории,
оборванная цепочка задач."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import as_utc, utcnow
from app.models.category_meta import CategoryMeta, MetaRun

ACTIVE_STATUSES = ("queued", "in_work")
# Худший случай одной категории — ~27 минут (CATEGORY_SOFT_LIMIT в app/tasks.py).
STUCK_AFTER = timedelta(minutes=30)
# Цепочка может честно стоять в очереди Celery за партией статей часами — поэтому
# «оборвалась» только после двух часов без единого признака жизни.
STALE_AFTER = timedelta(hours=2)


@dataclass
class RunProgress:
    total: int
    done: int
    failed: int
    wait_until: datetime | None
    stale: bool


def is_stuck(category: CategoryMeta, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return (category.status == "in_work" and category.started_at is not None
            and as_utc(category.started_at) < now - STUCK_AFTER)


def active_run(db: Session, site_id: int) -> MetaRun | None:
    return db.scalars(select(MetaRun).where(MetaRun.site_id == site_id,
                                            MetaRun.finished_at.is_(None))
                      .order_by(MetaRun.id.desc())).first()


def last_run(db: Session, site_id: int) -> MetaRun | None:
    return db.scalars(select(MetaRun).where(MetaRun.site_id == site_id,
                                            MetaRun.finished_at.is_not(None))
                      .order_by(MetaRun.finished_at.desc())).first()


def last_successful_run(db: Session, site_id: int) -> MetaRun | None:
    return db.scalars(select(MetaRun).where(MetaRun.site_id == site_id,
                                            MetaRun.finished_at.is_not(None),
                                            MetaRun.error_text == "")
                      .order_by(MetaRun.finished_at.desc())).first()


def next_queued(db: Session, site_id: int) -> CategoryMeta | None:
    return db.scalars(select(CategoryMeta).where(CategoryMeta.site_id == site_id,
                                                 CategoryMeta.status == "queued")
                      .order_by(CategoryMeta.id)).first()


def _active_categories(db: Session, site_id: int) -> list[CategoryMeta]:
    return list(db.scalars(select(CategoryMeta).where(
        CategoryMeta.site_id == site_id, CategoryMeta.status.in_(ACTIVE_STATUSES))).all())


def is_stale(db: Session, run: MetaRun, now: datetime | None = None) -> bool:
    if run.finished_at is not None:
        return False
    now = now or utcnow()
    categories = _active_categories(db, run.site_id)
    for category in categories:
        if category.status == "in_work" and not is_stuck(category, now):
            return False
        if (category.status == "queued" and category.wait_until is not None
                and as_utc(category.wait_until) > now - STALE_AFTER):
            return False
    moments = [as_utc(run.started_at)] + [as_utc(c.updated_at) for c in categories if c.updated_at]
    return max(moments) < now - STALE_AFTER


def run_progress(db: Session, run: MetaRun, now: datetime | None = None) -> RunProgress:
    now = now or utcnow()

    def count(status: str) -> int:
        return db.scalar(select(func.count()).select_from(CategoryMeta).where(
            CategoryMeta.site_id == run.site_id, CategoryMeta.status == status)) or 0

    waits = [as_utc(c.wait_until) for c in _active_categories(db, run.site_id)
             if c.status == "queued" and c.wait_until is not None and as_utc(c.wait_until) > now]
    return RunProgress(total=run.total, done=count("done"), failed=count("failed"),
                       wait_until=min(waits) if waits else None, stale=is_stale(db, run, now))


def finish_run_if_complete(db: Session, site_id: int, now: datetime | None = None) -> MetaRun | None:
    """Закрывает открытый запуск, если у сайта не осталось queued/in_work.
    Возвращает закрытый запуск или None."""
    run = active_run(db, site_id)
    if run is None or _active_categories(db, site_id):
        return None
    run.finished_at = now or utcnow()
    db.commit()
    return run
```

- [ ] **Step 4: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_category_meta_runs.py`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/category_meta/runs.py execution/backend/tests/test_category_meta_runs.py
git commit -m "feat: прогресс запуска метатегов, зависшие категории и оборванная цепочка"
```

---

### Task 14: Фоновые задачи — цепочка категорий

**Files:**
- Modify: `execution/backend/app/tasks.py`
- Test: `execution/backend/tests/test_tasks_category_meta.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_tasks_category_meta.py`:

```python
from types import SimpleNamespace

import pytest

from app.ai.factory import AIConfigError
from app.ai.text import JsonResult, LLMError
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.job import JobRun, LlmUsage
from app.models.site import Site
from app.sites.client import SiteAPIError
from app.tasks import (
    MAX_QUOTA_COUNTDOWN_SECONDS, SEED_MISSING_TEXT, generate_category_meta_sync,
    start_meta_run_sync,
)
from app.wordstat.client import WordstatAuthError, WordstatError
from app.wordstat.quota import QuotaExceeded

CATEGORIES = [
    {"id": 45, "name": "Листовые материалы", "slug": "listovye-materialy", "parent": None,
     "published": True},
    {"id": 46, "name": "Фанера", "slug": "fanera", "parent": 45, "published": True},
    {"id": 70, "name": "Водосток", "slug": "vodostok", "parent": None, "published": False},
]


@pytest.fixture
def site(db_session):
    from app.seed import seed_prompts

    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               city="Москва", city_in="в Москве", wordstat_region_id=213, brand="Стройбаза",
               meta_enabled=True)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def run(db_session, site, admin):
    row = MetaRun(site_id=site.id, created_by_id=admin.id)
    db_session.add(row)
    db_session.commit()
    return row


def seeds_answer(ids):
    return [{"id": i, "phrase": f"ф{i}", "nominative": f"ф{i}", "buy": f"купить ф{i}",
             "price": f"цена ф{i}"} for i in ids]


def patch_start(monkeypatch, *, answer=None, categories=CATEGORIES, list_error=None):
    def list_catalog_categories():
        if list_error:
            raise list_error
        return categories

    client = SimpleNamespace(list_catalog_categories=list_catalog_categories,
                             fetch_sitemap_paths=lambda: {"/catalog/listovye-materialy/",
                                                          "/catalog/category/listovye-materialy/fanera/"})
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: client)
    text = SimpleNamespace(model="m", prompts=[])

    def complete_json(prompt):
        text.prompts.append(prompt)
        return JsonResult(answer if answer is not None else seeds_answer([45, 46]), 10, 5, 0.2)

    text.complete_json = complete_json
    monkeypatch.setattr("app.tasks.build_text_client", lambda db: text)
    return text


def categories_by_remote(db, site):
    return {c.remote_id: c for c in db.query(CategoryMeta).filter_by(site_id=site.id).all()}


def test_start_queues_categories_and_returns_first(db_session, site, run, monkeypatch):
    patch_start(monkeypatch)
    first = start_meta_run_sync(db_session, run.id)
    rows = categories_by_remote(db_session, site)
    assert first == rows[45].id
    assert (rows[45].status, rows[46].status, rows[70].status) == ("queued", "queued", "skipped")
    assert rows[46].form_buy == "купить ф46"
    assert run.total == 2 and run.finished_at is None
    job = db_session.get(JobRun, run.job_run_id)
    assert job.kind == "category_meta_run" and job.status == "running"
    assert [u.cost for u in db_session.query(LlmUsage).all()] == [0.2]


def test_start_does_not_ask_llm_for_known_seeds(db_session, site, run, monkeypatch):
    for remote_id, name in ((45, "Листовые материалы"), (46, "Фанера")):
        db_session.add(CategoryMeta(site_id=site.id, remote_id=remote_id, name=name,
                                    seed_phrase="x", seed_source_name=name, status="done"))
    db_session.commit()
    text = patch_start(monkeypatch)
    start_meta_run_sync(db_session, run.id)
    assert text.prompts == []
    assert categories_by_remote(db_session, site)[46].status == "queued"


def test_start_marks_categories_without_seed_failed(db_session, site, run, monkeypatch):
    patch_start(monkeypatch, answer=seeds_answer([46]))
    first = start_meta_run_sync(db_session, run.id)
    rows = categories_by_remote(db_session, site)
    assert (rows[45].status, rows[45].error_text) == ("failed", SEED_MISSING_TEXT)
    assert first == rows[46].id


def test_start_site_error_fails_run(db_session, site, run, monkeypatch):
    patch_start(monkeypatch, list_error=SiteAPIError("список категорий каталога: HTTP 403"))
    assert start_meta_run_sync(db_session, run.id) is None
    assert run.finished_at is not None
    assert run.error_text == "список категорий каталога: HTTP 403"
    assert db_session.get(JobRun, run.job_run_id).status == "failed"


def test_start_with_nothing_to_do_closes_run(db_session, site, run, monkeypatch):
    patch_start(monkeypatch, categories=[CATEGORIES[2]])
    assert start_meta_run_sync(db_session, run.id) is None
    assert run.finished_at is not None and run.error_text == ""


# --- задача категории ---

@pytest.fixture
def queued(db_session, site):
    rows = [CategoryMeta(site_id=site.id, remote_id=i, name=f"К{i}", status="queued",
                         seed_phrase=f"ф{i}", form_nominative=f"ф{i}", form_buy=f"купить ф{i}",
                         url=f"/catalog/k{i}/") for i in (1, 2, 3)]
    db_session.add_all(rows)
    db_session.commit()
    return rows


def patch_category(monkeypatch, behaviour):
    """behaviour(category, kwargs) — вместо generate_category."""
    monkeypatch.setattr("app.tasks.build_text_client", lambda db: SimpleNamespace(model="m"))
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    def fake_generate(db, category, site, **kwargs):
        behaviour(category, kwargs)
        db.commit()

    monkeypatch.setattr("app.tasks.generate_category", fake_generate)


def succeed(category, kwargs):
    kwargs["record_usage"](10, 5, 0.1)
    category.status = "done"


def test_success_continues_chain_and_logs_to_run_job(db_session, site, queued, monkeypatch):
    job = JobRun(kind="category_meta_run", site_id=site.id)
    db_session.add(job)
    db_session.commit()
    run = MetaRun(site_id=site.id, job_run_id=job.id, total=3)
    db_session.add(run)
    db_session.commit()
    patch_category(monkeypatch, succeed)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, queued[1].id)
    assert queued[0].status == "done"
    assert [u.job_run_id for u in db_session.query(LlmUsage).all()] == [job.id]
    assert run.finished_at is None


def test_last_category_closes_run_and_job(db_session, site, queued, monkeypatch):
    queued[1].status = queued[2].status = "done"
    job = JobRun(kind="category_meta_run", site_id=site.id)
    db_session.add(job)
    db_session.commit()
    run = MetaRun(site_id=site.id, job_run_id=job.id, total=3)
    db_session.add(run)
    db_session.commit()
    patch_category(monkeypatch, succeed)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, None)
    assert run.finished_at is not None
    assert (job.status, job.log_text) == ("ok", "готово 3/3, ошибок 0")


def test_quota_requeues_with_capped_countdown(db_session, site, queued, monkeypatch):
    def no_quota(category, kwargs):
        raise QuotaExceeded(2400)

    patch_category(monkeypatch, no_quota)
    countdown, following = generate_category_meta_sync(db_session, queued[0].id)
    assert (countdown, following) == (MAX_QUOTA_COUNTDOWN_SECONDS, None)
    assert queued[0].status == "queued"
    assert queued[0].wait_until is not None


def test_category_error_is_recorded_and_chain_goes_on(db_session, site, queued, monkeypatch):
    def llm_down(category, kwargs):
        raise LLMError("LLM недоступна после 3 попыток")

    patch_category(monkeypatch, llm_down)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, queued[1].id)
    assert (queued[0].status, queued[0].error_text) == ("failed", "LLM недоступна после 3 попыток")


def test_wordstat_error_fails_only_this_category(db_session, site, queued, monkeypatch):
    def wordstat_down(category, kwargs):
        raise WordstatError("Wordstat topRequests: HTTP 503")

    patch_category(monkeypatch, wordstat_down)
    generate_category_meta_sync(db_session, queued[0].id)
    assert [c.status for c in queued] == ["failed", "queued", "queued"]


@pytest.mark.parametrize("error", [WordstatAuthError("ключ Wordstat отклонён", 401),
                                   AIConfigError("ключ RouterAI не задан")])
def test_config_error_stops_whole_run(db_session, site, queued, monkeypatch, error):
    run = MetaRun(site_id=site.id, total=3)
    db_session.add(run)
    db_session.commit()

    def broken(category, kwargs):
        raise error

    patch_category(monkeypatch, broken)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, None)
    assert [c.status for c in queued] == ["failed", "failed", "failed"]
    assert all(c.error_text == str(error) for c in queued)
    assert run.finished_at is not None


def test_unexpected_error_does_not_break_chain(db_session, site, queued, monkeypatch):
    def boom(category, kwargs):
        raise KeyError("id")

    patch_category(monkeypatch, boom)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, queued[1].id)
    assert queued[0].error_text == "непредвиденная ошибка: KeyError: 'id'"


def test_already_taken_category_is_skipped_but_chain_continues(db_session, site, queued,
                                                               monkeypatch):
    queued[0].status = "in_work"
    db_session.commit()
    patch_category(monkeypatch, lambda category, kwargs: pytest.fail("не должна запускаться"))
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, queued[1].id)
    assert generate_category_meta_sync(db_session, queued[0].id, continue_run=False) == (None, None)


def test_regenerate_uses_own_job_and_does_not_continue(db_session, site, queued, monkeypatch):
    queued[1].status = queued[2].status = "done"
    db_session.commit()
    patch_category(monkeypatch, succeed)
    assert generate_category_meta_sync(db_session, queued[0].id, continue_run=False) == (None, None)
    job = db_session.query(JobRun).filter_by(kind="category_meta").one()
    assert job.status == "ok"
    assert db_session.query(LlmUsage).one().job_run_id == job.id


def test_regenerate_without_llm_call_creates_no_job(db_session, site, queued, monkeypatch):
    def no_quota(category, kwargs):
        raise QuotaExceeded(30)

    patch_category(monkeypatch, no_quota)
    assert generate_category_meta_sync(db_session, queued[0].id, continue_run=False) == (30, None)
    assert db_session.query(JobRun).count() == 0
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_tasks_category_meta.py`
Expected: FAIL — `ImportError: cannot import name 'MAX_QUOTA_COUNTDOWN_SECONDS' from 'app.tasks'`.

- [ ] **Step 3: Импорты `app/tasks.py`**

Заменить начало файла после докстринга:

```python
from __future__ import annotations

import logging
from datetime import timedelta

from celery import current_task
from celery.exceptions import SoftTimeLimitExceeded
```

После `from app.articles.topics import filter_duplicates`:

```python
from app.category_meta.generator import MetaValidationError, generate_category
from app.category_meta.runs import active_run, finish_run_if_complete, next_queued
from app.category_meta.seeds import SeedsError, generate_seeds, needs_seed
from app.category_meta.tree import sync_categories
```

Заменить `from app.models.job import JobRun` на:

```python
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.job import JobRun, LlmUsage
```

После `from app.sites.client import SiteAPIError`:

```python
from app.wordstat.client import WordstatAuthError, WordstatError
from app.wordstat.factory import (
    WordstatConfigError, build_wordstat_client, hourly_limit, stoplist,
)
from app.wordstat.quota import QuotaExceeded

logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Задачи — в конец `app/tasks.py`**

```python
# --- метатеги категорий (directions/2026-09-16-category-meta-design.md) ---
#
# Цепочка: запуск ставит в Celery только первую категорию, каждая задача по
# завершении — следующую. Проект занимает не больше одного слота воркера из двух.

# Худший случай категории: Wordstat 3 запроса × (30 с × 3 попытки + паузы 2+4)
# = 288 с; LLM 2 попытки × 366 с = 732 с; запись на сайт 3 попытки ×
# (список метатегов 120 с + запись 60 с) + паузы 1+2 = 543 с. Итого ≈ 1563 с.
CATEGORY_SOFT_LIMIT = 1600
CATEGORY_HARD_LIMIT = 1780
# Подготовка запуска: страницы категорий и sitemap (~360 с) + фразы LLM пачками
# по 60 категорий (366 с на пачку). 2400 с хватает на ~250 категорий.
RUN_START_SOFT_LIMIT = 2400
RUN_START_HARD_LIMIT = 2580
# У Redis-брокера visibility_timeout — час: задача с ETA дольше него
# доставляется повторно. Поэтому ждём квоту кусками не больше 10 минут.
MAX_QUOTA_COUNTDOWN_SECONDS = 600

SEED_MISSING_TEXT = ("модель не вернула поисковую фразу для категории — "
                     "запустите обновление ещё раз")


def _record_usage(db, job_run_id: int, model: str, tokens_prompt: int,
                  tokens_completion: int, cost: float) -> None:
    db.add(LlmUsage(job_run_id=job_run_id, kind="text", model=model,
                    tokens_prompt=tokens_prompt, tokens_completion=tokens_completion, cost=cost))
    db.commit()


def _close_run_job(db, run: MetaRun) -> None:
    job = db.get(JobRun, run.job_run_id) if run.job_run_id else None
    if job is None or job.finished_at is not None:
        return
    if run.error_text:
        _finish_job(db, job, "failed", run.error_text)
        return
    failed = db.query(CategoryMeta).filter(CategoryMeta.site_id == run.site_id,
                                           CategoryMeta.status == "failed").count()
    done = db.query(CategoryMeta).filter(CategoryMeta.site_id == run.site_id,
                                         CategoryMeta.status == "done").count()
    _finish_job(db, job, "ok" if not failed else "failed",
                f"готово {done}/{run.total}, ошибок {failed}")


def _complete_run(db, site_id: int) -> None:
    run = finish_run_if_complete(db, site_id)
    if run is not None:
        _close_run_job(db, run)


def _fail_run(db, run: MetaRun, text: str) -> None:
    run.error_text = text
    run.finished_at = utcnow()
    db.commit()
    _close_run_job(db, run)


def start_meta_run_sync(db, run_id: int) -> int | None:
    """Синхронизация дерева и фразы; возвращает id первой категории для цепочки."""
    run = db.get(MetaRun, run_id)
    site = db.get(Site, run.site_id)
    job = _start_job(db, "category_meta_run", site.id, run.created_by_id, {"run_id": run_id})
    run.job_run_id = job.id
    db.commit()
    try:
        synced = sync_categories(db, site, open_site_client(db, site))
        pending = [row for row in synced.active if needs_seed(row)]
        missing_ids: set[int] = set()
        if pending:
            text_client = build_text_client(db)
            missing = generate_seeds(
                db, site, pending, text_client,
                lambda tp, tc, cost: _record_usage(db, job.id, text_client.model, tp, tc, cost))
            missing_ids = {row.id for row in missing}
        for row in synced.active:
            row.wait_until = None
            row.started_at = None
            row.updated_at = utcnow()
            if row.id in missing_ids or not row.seed_phrase:
                row.status, row.error_text = "failed", SEED_MISSING_TEXT
            else:
                row.status, row.error_text = "queued", ""
        run.total = len(synced.active)
        db.commit()
    except SoftTimeLimitExceeded:
        db.rollback()
        _fail_run(db, run, "превышен лимит времени подготовки запуска")
        return None
    except (SiteAPIError, LLMError, PromptError, SeedsError, AIConfigError,
            SecretDecryptionError) as exc:
        db.rollback()
        _fail_run(db, run, str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 — барьер, см. run_batch_sync
        db.rollback()
        _fail_run(db, run, f"непредвиденная ошибка: {type(exc).__name__}: {exc}")
        raise

    first = next_queued(db, site.id)
    if first is None:
        _complete_run(db, site.id)
        return None
    return first.id


def _fail_category(db, category: CategoryMeta, text: str) -> None:
    category.status = "failed"
    category.error_text = text
    category.wait_until = None
    category.updated_at = utcnow()
    db.commit()


def _fail_queued(db, site_id: int, text: str) -> None:
    """Ошибка конфигурации (ключ Wordstat, RouterAI) — одна на весь запуск:
    остальные категории в очереди получают тот же текст, запуск закрывается."""
    for row in db.query(CategoryMeta).filter(CategoryMeta.site_id == site_id,
                                             CategoryMeta.status == "queued").all():
        row.status, row.error_text, row.wait_until = "failed", text, None
        row.updated_at = utcnow()
    db.commit()


def generate_category_meta_sync(db, category_id: int,
                                continue_run: bool = True) -> tuple[float | None, int | None]:
    """Возвращает (countdown для повтора этой же задачи, id следующей категории цепочки)."""
    category = db.get(CategoryMeta, category_id)
    if category is None:
        return None, None
    site = db.get(Site, category.site_id)
    if category.status != "queued":
        # Категорию уже взяла другая задача (перегенерация или вторая цепочка
        # после перезапуска) — не обрабатываем дважды, но цепочку не рвём.
        if not continue_run:
            return None, None
        following = next_queued(db, site.id)
        return None, (following.id if following and following.id != category_id else None)

    run = active_run(db, site.id) if continue_run else None
    own_job: JobRun | None = None

    def job_id() -> int:
        nonlocal own_job
        if run is not None and run.job_run_id is not None:
            return run.job_run_id
        if own_job is None:
            own_job = _start_job(db, "category_meta", site.id, None, {"category_id": category_id})
        return own_job.id

    category.status = "in_work"
    category.started_at = utcnow()
    category.updated_at = utcnow()
    db.commit()

    stop_run_text = ""
    try:
        text_client = build_text_client(db)
        generate_category(
            db, category, site,
            wordstat_factory=lambda: build_wordstat_client(db),
            text_client=text_client, site_client=open_site_client(db, site),
            limit=hourly_limit(db), stoplist=stoplist(db),
            record_usage=lambda tp, tc, cost: _record_usage(db, job_id(), text_client.model,
                                                            tp, tc, cost))
    except QuotaExceeded as wait:
        db.rollback()
        category.status = "queued"
        category.wait_until = utcnow() + timedelta(seconds=wait.seconds)
        category.updated_at = utcnow()
        db.commit()
        return min(wait.seconds, MAX_QUOTA_COUNTDOWN_SECONDS), None
    except SoftTimeLimitExceeded:
        db.rollback()
        _fail_category(db, category, "превышен лимит времени задачи")
    except (WordstatAuthError, WordstatConfigError, AIConfigError, SecretDecryptionError) as exc:
        db.rollback()
        _fail_category(db, category, str(exc))
        stop_run_text = str(exc)
    except (WordstatError, LLMError, PromptError, SiteAPIError, MetaValidationError,
            SeedsError) as exc:
        db.rollback()
        _fail_category(db, category, str(exc))
    except Exception as exc:  # noqa: BLE001 — барьер: цепочка не должна рваться
        logger.exception("метатеги категории %s: непредвиденная ошибка", category_id)
        db.rollback()
        _fail_category(db, category, f"непредвиденная ошибка: {type(exc).__name__}: {exc}")

    if own_job is not None:
        _finish_job(db, own_job, "ok" if category.status == "done" else "failed",
                    category.error_text)
    if stop_run_text:
        _fail_queued(db, site.id, stop_run_text)
    _complete_run(db, site.id)
    if not continue_run:
        return None, None
    following = next_queued(db, site.id)
    return None, (following.id if following else None)


def enqueue_category_meta(category_id: int, *, continue_run: bool = True,
                          countdown: float = 0) -> None:
    generate_category_meta.apply_async(
        args=[category_id], kwargs={"continue_run": continue_run}, countdown=countdown,
        soft_time_limit=CATEGORY_SOFT_LIMIT, time_limit=CATEGORY_HARD_LIMIT)


def enqueue_meta_run(run_id: int) -> None:
    start_meta_run.apply_async(args=[run_id], soft_time_limit=RUN_START_SOFT_LIMIT,
                               time_limit=RUN_START_HARD_LIMIT)


@celery_app.task(name="app.tasks.start_meta_run")
def start_meta_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        first = start_meta_run_sync(db, run_id)
    finally:
        db.close()
    if first is not None:
        enqueue_category_meta(first)


@celery_app.task(name="app.tasks.generate_category_meta")
def generate_category_meta(category_id: int, continue_run: bool = True) -> None:
    db = SessionLocal()
    try:
        countdown, following = generate_category_meta_sync(db, category_id, continue_run)
    finally:
        db.close()
    if countdown:
        enqueue_category_meta(category_id, continue_run=continue_run, countdown=countdown)
    elif following is not None:
        enqueue_category_meta(following)
```

- [ ] **Step 5: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_tasks_category_meta.py tests/test_tasks.py`
Expected: PASS (56 passed).

- [ ] **Step 6: Commit**

```bash
git add execution/backend/app/tasks.py execution/backend/tests/test_tasks_category_meta.py
git commit -m "feat: фоновое обновление метатегов цепочкой задач с ожиданием квоты"
```

---

### Task 15: API раздела

**Files:**
- Create: `execution/backend/app/api/category_meta.py`
- Modify: `execution/backend/app/main.py`
- Test: `execution/backend/tests/test_api_category_meta.py`

- [ ] **Step 1: Тест (красный)**

`tests/test_api_category_meta.py`:

```python
from datetime import timedelta

import pytest

from app.ai.factory import AIConfigError
from app.clock import utcnow
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.job import JobRun
from app.models.site import Site
from app.wordstat.quota import QuotaExceeded
from app.wordstat.regions import Region


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза Москва", domain="stroybaza-moscow.ru",
               base_url="https://stroybaza-moscow.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def project(db_session, site):
    site.meta_enabled, site.city, site.city_in = True, "Москва", "в Москве"
    site.brand, site.wordstat_region_id = "Стройбаза", 213
    db_session.commit()
    return site


@pytest.fixture
def enqueued(monkeypatch):
    calls = {"runs": [], "categories": []}
    monkeypatch.setattr("app.api.category_meta.enqueue_meta_run",
                        lambda run_id: calls["runs"].append(run_id))
    monkeypatch.setattr("app.api.category_meta.enqueue_category_meta",
                        lambda category_id, **kw: calls["categories"].append((category_id, kw)))
    return calls


def payload(site, **overrides):
    return {"site_id": site.id, "city": "Москва", "city_in": "в Москве", "brand": "Стройбаза",
            "wordstat_region_id": 213, **overrides}


def test_requires_login(client):
    assert client.get("/api/category-meta/projects").status_code == 401


def test_create_project_with_city_in(manager_client, site):
    resp = manager_client.post("/api/category-meta/projects", json=payload(site))
    assert resp.status_code == 200
    body = resp.json()
    assert (body["city_in"], body["brand"], body["updated_at"], body["run"]) == \
        ("в Москве", "Стройбаза", None, None)
    assert [p["site_id"] for p in manager_client.get("/api/category-meta/projects").json()] == [site.id]


def test_create_project_fills_city_in_by_llm(manager_client, site, monkeypatch):
    monkeypatch.setattr("app.api.category_meta.build_text_client", lambda db, max_retries=None: object())
    monkeypatch.setattr("app.api.category_meta.city_in_for", lambda client, city: "во Владимире")
    resp = manager_client.post("/api/category-meta/projects",
                               json=payload(site, city="Владимир", city_in="", wordstat_region_id=192))
    assert resp.json()["city_in"] == "во Владимире"


def test_create_project_without_routerai_asks_manual_city_in(manager_client, site, monkeypatch):
    def no_key(db, max_retries=None):
        raise AIConfigError("ключ RouterAI не задан")

    monkeypatch.setattr("app.api.category_meta.build_text_client", no_key)
    resp = manager_client.post("/api/category-meta/projects", json=payload(site, city_in=""))
    assert resp.status_code == 400
    assert "вручную" in resp.json()["detail"]


def test_create_project_validates(manager_client, site):
    assert manager_client.post("/api/category-meta/projects",
                               json=payload(site, brand=" ")).status_code == 400
    assert manager_client.post("/api/category-meta/projects",
                               json=payload(site, site_id=999)).status_code == 404
    manager_client.post("/api/category-meta/projects", json=payload(site))
    assert manager_client.post("/api/category-meta/projects",
                               json=payload(site)).status_code == 400


def test_update_project_city_change_recomputes_city_in(manager_client, project, monkeypatch):
    monkeypatch.setattr("app.api.category_meta.build_text_client", lambda db, max_retries=None: object())
    monkeypatch.setattr("app.api.category_meta.city_in_for", lambda client, city: "в Твери")
    resp = manager_client.put(f"/api/category-meta/projects/{project.id}",
                              json=payload(project, city="Тверь", wordstat_region_id=14))
    assert resp.json()["city_in"] == "в Твери"


def test_delete_project_keeps_categories(manager_client, project, db_session):
    db_session.add(CategoryMeta(site_id=project.id, remote_id=46, name="Фанера"))
    db_session.commit()
    assert manager_client.delete(f"/api/category-meta/projects/{project.id}").status_code == 200
    assert manager_client.get("/api/category-meta/projects").json() == []
    assert db_session.query(CategoryMeta).count() == 1


def test_regions_search(manager_client, monkeypatch):
    monkeypatch.setattr("app.api.category_meta.load_regions", lambda db, limit, factory: [
        Region(213, "Москва", "Россия / Москва"), Region(1, "Москва и область", "Россия / …")])
    body = manager_client.get("/api/category-meta/regions", params={"q": "москва"}).json()
    assert [r["id"] for r in body] == [213, 1]


def test_regions_quota_exhausted(manager_client, monkeypatch):
    def no_quota(db, limit, factory):
        raise QuotaExceeded(125)

    monkeypatch.setattr("app.api.category_meta.load_regions", no_quota)
    resp = manager_client.get("/api/category-meta/regions", params={"q": "москва"})
    assert resp.status_code == 429
    assert "через 3 мин" in resp.json()["detail"]


def test_regions_without_key(manager_client):
    resp = manager_client.get("/api/category-meta/regions", params={"q": "москва"})
    assert resp.status_code == 400
    assert "wordstat_api_key" in resp.json()["detail"]


def test_run_creates_run_and_enqueues(manager_client, project, enqueued, db_session):
    resp = manager_client.post(f"/api/category-meta/projects/{project.id}/run")
    assert resp.status_code == 200
    run = db_session.query(MetaRun).one()
    assert enqueued["runs"] == [run.id]
    assert resp.json()["run"]["id"] == run.id


def test_run_requires_complete_project(manager_client, project, enqueued, db_session):
    project.wordstat_region_id = None
    db_session.commit()
    assert manager_client.post(f"/api/category-meta/projects/{project.id}/run").status_code == 400


def test_second_run_while_active_is_conflict(manager_client, project, enqueued, db_session):
    manager_client.post(f"/api/category-meta/projects/{project.id}/run")
    assert manager_client.post(f"/api/category-meta/projects/{project.id}/run").status_code == 409
    assert len(enqueued["runs"]) == 1


def test_stale_run_can_be_restarted(manager_client, project, enqueued, db_session):
    job = JobRun(kind="category_meta_run", site_id=project.id)
    db_session.add(job)
    db_session.commit()
    old = MetaRun(site_id=project.id, job_run_id=job.id, started_at=utcnow() - timedelta(hours=5))
    db_session.add_all([old, CategoryMeta(site_id=project.id, remote_id=1, name="К", status="queued",
                                          updated_at=utcnow() - timedelta(hours=4))])
    db_session.commit()
    project_body = manager_client.get("/api/category-meta/projects").json()[0]
    assert project_body["run"]["stale"] is True
    assert manager_client.post(f"/api/category-meta/projects/{project.id}/run").status_code == 200
    db_session.refresh(old)
    assert old.finished_at is not None and old.error_text.startswith("прерван")
    db_session.refresh(job)
    assert job.status == "failed"
    assert len(enqueued["runs"]) == 1


def test_project_progress_and_last_update(manager_client, project, db_session):
    finished = utcnow() - timedelta(days=1)
    db_session.add_all([
        MetaRun(site_id=project.id, total=2, started_at=finished - timedelta(hours=2),
                finished_at=finished),
        MetaRun(site_id=project.id, total=3),
        CategoryMeta(site_id=project.id, remote_id=1, name="А", status="done"),
        CategoryMeta(site_id=project.id, remote_id=2, name="Б", status="queued",
                     wait_until=utcnow() + timedelta(minutes=20)),
        CategoryMeta(site_id=project.id, remote_id=3, name="В", status="failed"),
    ])
    db_session.commit()
    body = manager_client.get("/api/category-meta/projects").json()[0]
    assert body["updated_at"] is not None
    assert (body["run"]["total"], body["run"]["done"], body["run"]["failed"]) == (3, 1, 1)
    assert body["run"]["wait_until"] is not None and body["run"]["stale"] is False


def test_categories_list(manager_client, project, db_session):
    db_session.add_all([
        CategoryMeta(site_id=project.id, remote_id=46, remote_parent_id=45, name="Фанера",
                     path="Листовые материалы / Фанера",
                     url="/catalog/category/listovye-materialy/fanera/", status="done",
                     title="Фанера в Москве | Стройбаза", previous_json={"h1": "старый"}),
        CategoryMeta(site_id=project.id, remote_id=45, name="Листовые материалы",
                     path="Листовые материалы", url="/catalog/listovye-materialy/",
                     status="in_work", started_at=utcnow() - timedelta(hours=1)),
    ])
    db_session.commit()
    body = manager_client.get(f"/api/category-meta/projects/{project.id}/categories").json()
    assert [c["remote_id"] for c in body] == [45, 46]
    assert body[0]["stuck"] is True
    assert body[1]["page_url"] == \
        "https://stroybaza-moscow.ru/catalog/category/listovye-materialy/fanera/"
    assert body[1]["previous_json"] == {"h1": "старый"}


def category(db, project, **kwargs):
    row = CategoryMeta(site_id=project.id, remote_id=46, name="Фанера", seed_phrase="фанера",
                       **kwargs)
    db.add(row)
    db.commit()
    return row


def test_regenerate_enqueues_single_category(manager_client, project, enqueued, db_session):
    row = category(db_session, project, status="done")
    resp = manager_client.post(f"/api/category-meta/categories/{row.id}/regenerate")
    assert resp.status_code == 200 and resp.json()["status"] == "queued"
    assert enqueued["categories"] == [(row.id, {"continue_run": False})]


@pytest.mark.parametrize("status,started_hours_ago,code", [
    ("queued", None, 409), ("in_work", 0, 409), ("in_work", 2, 200), ("failed", None, 200),
    ("skipped", None, 400),
])
def test_regenerate_rules(manager_client, project, enqueued, db_session, status,
                          started_hours_ago, code):
    extra = {}
    if started_hours_ago is not None:
        extra["started_at"] = utcnow() - timedelta(hours=started_hours_ago)
    row = category(db_session, project, status=status, **extra)
    assert manager_client.post(f"/api/category-meta/categories/{row.id}/regenerate").status_code == code


def test_regenerate_without_seed(manager_client, project, enqueued, db_session):
    row = CategoryMeta(site_id=project.id, remote_id=46, name="Фанера", status="new")
    db_session.add(row)
    db_session.commit()
    assert manager_client.post(f"/api/category-meta/categories/{row.id}/regenerate").status_code == 400
```

- [ ] **Step 2: Убедиться, что падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_api_category_meta.py`
Expected: FAIL — 404 на `/api/category-meta/...` (роутера нет) и `AttributeError` у monkeypatch `app.api.category_meta`.

- [ ] **Step 3: Роутер — `app/api/category_meta.py`**

```python
"""API раздела «Метатеги категорий» (directions/2026-09-16-category-meta-design.md).

Проект — это карточка сайта с meta_enabled=True; отдельной сущности нет.
Доступ — всем залогиненным, как у «Статей» и «Строителей»."""

from __future__ import annotations

import math
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.factory import AIConfigError, build_text_client
from app.ai.text import LLMError
from app.api.deps import get_current_user, get_db
from app.category_meta.city import CityFormError, city_in_for
from app.category_meta.runs import (
    active_run, is_stale, is_stuck, last_run, last_successful_run, run_progress,
)
from app.clock import utcnow
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.job import JobRun
from app.models.site import Site
from app.models.user import User
from app.tasks import enqueue_category_meta, enqueue_meta_run
from app.wordstat.client import WordstatError
from app.wordstat.factory import WordstatConfigError, build_wordstat_client, hourly_limit
from app.wordstat.quota import QuotaExceeded
from app.wordstat.regions import load_regions, search_regions

router = APIRouter(prefix="/api/category-meta", tags=["category-meta"])


class ProjectIn(BaseModel):
    site_id: int
    city: str
    city_in: str = ""        # пусто — определит LLM
    brand: str
    wordstat_region_id: int


class RunOut(BaseModel):
    id: int
    total: int
    done: int
    failed: int
    started_at: datetime
    wait_until: datetime | None
    stale: bool


class ProjectOut(BaseModel):
    site_id: int
    name: str
    domain: str
    base_url: str
    city: str
    city_in: str
    brand: str
    wordstat_region_id: int | None
    updated_at: datetime | None      # finished_at последнего успешного запуска
    last_error: str                  # ошибка последнего завершённого запуска, если была
    run: RunOut | None


class RegionOut(BaseModel):
    id: int
    label: str
    path: str


class CategoryOut(BaseModel):
    id: int
    remote_id: int
    remote_parent_id: int | None
    name: str
    path: str
    url: str
    page_url: str
    status: str
    skip_reason: str
    error_text: str
    stuck: bool
    seed_phrase: str
    form_nominative: str
    form_buy: str
    chosen_form: str
    nominative_count: int | None
    declined_count: int | None
    total_count: int | None
    low_demand: bool
    title: str
    h1: str
    meta_description: str
    meta_keywords: str
    ai_keywords: str
    previous_json: dict | None
    wait_until: datetime | None
    updated_at: datetime


def _project_out(db: Session, site: Site) -> ProjectOut:
    run = active_run(db, site.id)
    run_out = None
    if run is not None:
        progress = run_progress(db, run)
        run_out = RunOut(id=run.id, total=progress.total, done=progress.done,
                         failed=progress.failed, started_at=run.started_at,
                         wait_until=progress.wait_until, stale=progress.stale)
    finished = last_run(db, site.id)
    successful = last_successful_run(db, site.id)
    return ProjectOut(
        site_id=site.id, name=site.name, domain=site.domain, base_url=site.base_url,
        city=site.city, city_in=site.city_in, brand=site.brand,
        wordstat_region_id=site.wordstat_region_id,
        updated_at=successful.finished_at if successful else None,
        last_error=finished.error_text if finished else "", run=run_out)


def _category_out(site: Site, row: CategoryMeta) -> CategoryOut:
    return CategoryOut(
        id=row.id, remote_id=row.remote_id, remote_parent_id=row.remote_parent_id,
        name=row.name, path=row.path, url=row.url,
        page_url=f"{site.base_url.rstrip('/')}{row.url}" if row.url else "",
        status=row.status, skip_reason=row.skip_reason, error_text=row.error_text,
        stuck=is_stuck(row), seed_phrase=row.seed_phrase, form_nominative=row.form_nominative,
        form_buy=row.form_buy, chosen_form=row.chosen_form,
        nominative_count=row.nominative_count, declined_count=row.declined_count,
        total_count=row.total_count, low_demand=row.low_demand, title=row.title, h1=row.h1,
        meta_description=row.meta_description, meta_keywords=row.meta_keywords,
        ai_keywords=row.ai_keywords, previous_json=row.previous_json,
        wait_until=row.wait_until, updated_at=row.updated_at)


def _project_or_404(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None or not site.meta_enabled:
        raise HTTPException(404, "проект не найден")
    return site


def _apply_project(db: Session, site: Site, payload: ProjectIn) -> None:
    city, brand = payload.city.strip(), payload.brand.strip()
    if not city or not brand:
        raise HTTPException(400, "укажите город и бренд")
    city_in = " ".join(payload.city_in.split())
    # Город сменили, а поле «с предлогом» осталось прежним — оно от старого города.
    if not city_in or (city != site.city and city_in == site.city_in):
        try:
            city_in = city_in_for(build_text_client(db, max_retries=1), city)
        except AIConfigError as exc:
            raise HTTPException(400, f"{exc} — или впишите город с предлогом вручную") from exc
        except (LLMError, CityFormError) as exc:
            raise HTTPException(502, f"не удалось определить город с предлогом: {exc} — "
                                     f"впишите его вручную") from exc
    site.city, site.city_in, site.brand = city, city_in, brand
    site.wordstat_region_id = payload.wordstat_region_id
    site.meta_enabled = True


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    sites = db.scalars(select(Site).where(Site.meta_enabled.is_(True)).order_by(Site.name)).all()
    return [_project_out(db, site) for site in sites]


@router.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectIn, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    site = db.get(Site, payload.site_id)
    if site is None:
        raise HTTPException(404, "сайт не найден")
    if site.meta_enabled:
        raise HTTPException(400, "проект для этого сайта уже добавлен")
    _apply_project(db, site, payload)
    db.commit()
    return _project_out(db, site)


@router.put("/projects/{site_id}", response_model=ProjectOut)
def update_project(site_id: int, payload: ProjectIn, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    _apply_project(db, site, payload)
    db.commit()
    return _project_out(db, site)


@router.delete("/projects/{site_id}")
def delete_project(site_id: int, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    """Убирает сайт из раздела; категории и теги в панели остаются —
    на сайте они и так остаются."""
    site = _project_or_404(db, site_id)
    run = active_run(db, site.id)
    if run is not None and not is_stale(db, run):
        raise HTTPException(409, "идёт обновление метатегов — дождитесь окончания")
    site.meta_enabled = False
    db.commit()
    return {"ok": True}


@router.get("/regions", response_model=list[RegionOut])
def regions(q: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    try:
        found = load_regions(db, hourly_limit(db), lambda: build_wordstat_client(db))
    except WordstatConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except QuotaExceeded as exc:
        raise HTTPException(429, f"квота Wordstat на этот час исчерпана — повторите через "
                                 f"{math.ceil(exc.seconds / 60)} мин") from exc
    except WordstatError as exc:
        raise HTTPException(502, str(exc)) from exc
    return [RegionOut(id=r.id, label=r.label, path=r.path) for r in search_regions(found, q)]


@router.post("/projects/{site_id}/run", response_model=ProjectOut)
def run_project(site_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    if not (site.city_in and site.brand and site.wordstat_region_id):
        raise HTTPException(400, "заполните у проекта город, бренд и регион Wordstat")
    current = active_run(db, site.id)
    if current is not None:
        if not is_stale(db, current):
            raise HTTPException(409, "обновление метатегов уже идёт")
        current.error_text = "прерван: цепочка задач оборвалась, обновление запущено заново"
        current.finished_at = utcnow()
        job = db.get(JobRun, current.job_run_id) if current.job_run_id else None
        if job is not None and job.finished_at is None:
            job.status, job.log_text, job.finished_at = "failed", current.error_text, utcnow()
    run = MetaRun(site_id=site.id, created_by_id=user.id)
    db.add(run)
    db.commit()
    enqueue_meta_run(run.id)
    return _project_out(db, site)


@router.get("/projects/{site_id}/categories", response_model=list[CategoryOut])
def list_categories(site_id: int, db: Session = Depends(get_db),
                    _user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    rows = db.scalars(select(CategoryMeta).where(CategoryMeta.site_id == site.id)
                      .order_by(CategoryMeta.path, CategoryMeta.id)).all()
    return [_category_out(site, row) for row in rows]


@router.post("/categories/{category_id}/regenerate", response_model=CategoryOut)
def regenerate_category(category_id: int, db: Session = Depends(get_db),
                        _user: User = Depends(get_current_user)):
    row = db.get(CategoryMeta, category_id)
    if row is None:
        raise HTTPException(404, "категория не найдена")
    site = _project_or_404(db, row.site_id)
    if row.status == "skipped":
        raise HTTPException(400, f"категория пропущена: {row.skip_reason}")
    if not row.seed_phrase:
        raise HTTPException(400, "у категории ещё нет поисковой фразы — сначала обновите "
                                 "метатеги проекта целиком")
    if row.status == "queued" or (row.status == "in_work" and not is_stuck(row)):
        raise HTTPException(409, "категория уже в работе")
    row.status, row.error_text, row.wait_until = "queued", "", None
    row.updated_at = utcnow()
    db.commit()
    enqueue_category_meta(row.id, continue_run=False)
    return _category_out(site, row)
```

- [ ] **Step 4: Подключить в `app/main.py`**

В импорт `from app.api import (...)` добавить `category_meta,` после `auth,`; кортеж регистрации:

```python
for module in (auth, sites, admin_sites, admin_settings, admin_prompts,
               admin_users, article_batches, company_imports, company_batches,
               jobs, tasks_status, category_meta):
```

- [ ] **Step 5: Тесты зелёные**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_api_category_meta.py`
Expected: PASS (23 passed).

- [ ] **Step 6: Полный регресс бэкенда**

Run: `docker compose run --rm --no-deps backend pytest -q`
Expected: PASS (778 passed).

- [ ] **Step 7: Commit**

```bash
git add execution/backend/app/api/category_meta.py execution/backend/app/main.py execution/backend/tests/test_api_category_meta.py
git commit -m "feat: API раздела «Метатеги категорий»"
```

---

### Task 16: Фронтенд — раздел «Метатеги категорий»

**Files:**
- Modify: `execution/frontend/src/api.ts`, `src/statuses.ts`, `src/App.tsx`
- Create: `execution/frontend/src/pages/CategoryMetaPage.tsx`, `src/pages/CategoryMetaTree.tsx`

Автотестов у фронтенда в проекте нет — проверка сборкой (`tsc` строгий, `noUnusedLocals`) и живым прогоном в Task 19.

- [ ] **Step 1: `src/api.ts`**

Перед `export default api`:

```ts
// --- метатеги категорий ---
export interface MetaRunState {
  id: number; total: number; done: number; failed: number
  started_at: string; wait_until: string | null; stale: boolean
}
export interface MetaProject {
  site_id: number; name: string; domain: string; base_url: string
  city: string; city_in: string; brand: string; wordstat_region_id: number | null
  updated_at: string | null; last_error: string; run: MetaRunState | null
}
export interface MetaProjectIn {
  site_id: number; city: string; city_in: string; brand: string; wordstat_region_id: number
}
export interface WordstatRegion { id: number; label: string; path: string }
export interface CategoryMetaRow {
  id: number; remote_id: number; remote_parent_id: number | null
  name: string; path: string; url: string; page_url: string
  status: string; skip_reason: string; error_text: string; stuck: boolean
  seed_phrase: string; form_nominative: string; form_buy: string; chosen_form: string
  nominative_count: number | null; declined_count: number | null
  total_count: number | null; low_demand: boolean
  title: string; h1: string; meta_description: string; meta_keywords: string; ai_keywords: string
  previous_json: Record<string, string> | null
  wait_until: string | null; updated_at: string
}
export const getMetaProjects = () =>
  api.get<MetaProject[]>('/category-meta/projects').then(r => r.data)
export const createMetaProject = (d: MetaProjectIn) =>
  api.post<MetaProject>('/category-meta/projects', d).then(r => r.data)
export const updateMetaProject = (siteId: number, d: MetaProjectIn) =>
  api.put<MetaProject>(`/category-meta/projects/${siteId}`, d).then(r => r.data)
export const deleteMetaProject = (siteId: number) =>
  api.delete(`/category-meta/projects/${siteId}`)
export const searchWordstatRegions = (q: string) =>
  api.get<WordstatRegion[]>('/category-meta/regions', { params: { q } }).then(r => r.data)
export const runMetaProject = (siteId: number) =>
  api.post<MetaProject>(`/category-meta/projects/${siteId}/run`).then(r => r.data)
export const getMetaCategories = (siteId: number) =>
  api.get<CategoryMetaRow[]>(`/category-meta/projects/${siteId}/categories`).then(r => r.data)
export const regenerateMetaCategory = (id: number) =>
  api.post<CategoryMetaRow>(`/category-meta/categories/${id}/regenerate`).then(r => r.data)
```

- [ ] **Step 2: `src/statuses.ts`**

В конец файла:

```ts
/** Статусы категории в разделе «Метатеги категорий». */
export const CATEGORY_META_STATUS: Record<string, { color: string; label: string }> = {
  new: { color: 'default', label: 'Ещё не обрабатывалась' },
  queued: { color: 'default', label: 'В очереди' },
  in_work: { color: 'processing', label: 'В работе' },
  done: { color: 'success', label: 'Готово' },
  failed: { color: 'error', label: 'Ошибка' },
  skipped: { color: 'default', label: 'Пропущена' },
}
```

- [ ] **Step 3: `src/pages/CategoryMetaTree.tsx`**

```tsx
import { ReactNode, useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import {
  Alert, Button, Collapse, Descriptions, Drawer, Popconfirm, Space, Table, Tag, Typography, message,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { CategoryMetaRow, getMetaCategories, regenerateMetaCategory } from '../api'
import { CATEGORY_META_STATUS } from '../statuses'

type TreeRow = CategoryMetaRow & { children?: TreeRow[] }

function buildTree(rows: CategoryMetaRow[]): TreeRow[] {
  const byRemote = new Map<number, TreeRow>()
  rows.forEach(r => byRemote.set(r.remote_id, { ...r }))
  const roots: TreeRow[] = []
  byRemote.forEach(node => {
    const parent = node.remote_parent_id != null ? byRemote.get(node.remote_parent_id) : undefined
    if (parent) {
      if (!parent.children) parent.children = []
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  })
  return roots
}

function StatusTag({ row }: { row: CategoryMetaRow }) {
  if (row.stuck) return <Tag color="error">Похоже, зависла</Tag>
  const status = CATEGORY_META_STATUS[row.status]
  return (
    <Space size={4} wrap>
      <Tag color={status?.color}>{status?.label ?? row.status}</Tag>
      {row.status === 'done' && row.low_demand && <Tag color="warning">мало данных</Tag>}
    </Space>
  )
}

function withCount(text: string, max?: number): ReactNode {
  if (!text) return '—'
  return (
    <>
      {text}{' '}
      {max && (
        <Typography.Text type={text.length > max ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
          ({text.length}/{max})
        </Typography.Text>
      )}
    </>
  )
}

function formText(row: CategoryMetaRow): string {
  if (row.chosen_form === 'nominative') return `без склонения — «${row.form_nominative}»`
  if (row.chosen_form === 'declined') return `со склонением — «${row.form_buy}»`
  return '—'
}

function CategoryDrawer({ row, onClose, onChanged }: {
  row: CategoryMetaRow | null
  onClose: () => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  if (!row) return null

  const canRegenerate = Boolean(row.seed_phrase)
    && (['new', 'done', 'failed'].includes(row.status) || row.stuck)

  const regenerate = async () => {
    setBusy(true)
    try {
      await regenerateMetaCategory(row.id)
      message.success('Перегенерация поставлена в очередь')
      onChanged()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setBusy(false) }
  }

  const counts = row.nominative_count != null
    ? ` · «купить ${row.form_nominative}» ${row.nominative_count}, «${row.form_buy}» ${row.declined_count}`
    : ''
  const previous = row.previous_json && Object.keys(row.previous_json).length > 0
    ? row.previous_json : null

  return (
    <Drawer open width={600} title={row.name} onClose={onClose}
            extra={canRegenerate && (
              <Popconfirm title="Перегенерировать теги категории?"
                          description="Новые теги сразу запишутся на сайт."
                          onConfirm={regenerate}>
                <Button icon={<ReloadOutlined />} loading={busy}>Перегенерировать</Button>
              </Popconfirm>
            )}>
      <Typography.Paragraph type="secondary">
        {row.path}{row.page_url && <> · <a href={row.page_url} target="_blank" rel="noreferrer">страница</a></>}
      </Typography.Paragraph>
      {row.skip_reason && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message={`Пропущена: ${row.skip_reason}`} />
      )}
      {row.error_text && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message="Ошибка" description={row.error_text} />
      )}
      <Descriptions title="Теги" column={1} size="small" bordered>
        <Descriptions.Item label="title">{withCount(row.title, 70)}</Descriptions.Item>
        <Descriptions.Item label="h1">{withCount(row.h1, 60)}</Descriptions.Item>
        <Descriptions.Item label="description">{withCount(row.meta_description, 170)}</Descriptions.Item>
        <Descriptions.Item label="keywords">{withCount(row.meta_keywords)}</Descriptions.Item>
        <Descriptions.Item label="ai_keywords">{withCount(row.ai_keywords)}</Descriptions.Item>
      </Descriptions>
      <Descriptions title="Wordstat" column={1} size="small" bordered style={{ marginTop: 16 }}>
        <Descriptions.Item label="Фраза">{row.seed_phrase || '—'}</Descriptions.Item>
        <Descriptions.Item label="Форма">{formText(row)}{counts}</Descriptions.Item>
        <Descriptions.Item label="Запросов за 30 дней">{row.total_count ?? '—'}</Descriptions.Item>
      </Descriptions>
      {previous && (
        <Collapse style={{ marginTop: 16 }} items={[{
          key: 'previous', label: 'Было на сайте до нас',
          children: (
            <Descriptions column={1} size="small">
              {Object.entries(previous).map(([key, value]) => (
                <Descriptions.Item key={key} label={key}>{value || '—'}</Descriptions.Item>
              ))}
            </Descriptions>
          ),
        }]} />
      )}
    </Drawer>
  )
}

export default function CategoryMetaTree({ siteId, active }: { siteId: number; active: boolean }) {
  const [rows, setRows] = useState<CategoryMetaRow[] | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)

  const load = () => getMetaCategories(siteId).then(setRows)

  useEffect(() => { load() }, [siteId])

  useEffect(() => {
    const busy = active || (rows ?? []).some(r => r.status === 'queued' || r.status === 'in_work')
    if (!busy) return
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [rows, active])

  const tree = useMemo(() => buildTree(rows ?? []), [rows])

  if (rows === null) return <Typography.Text type="secondary">Загрузка…</Typography.Text>
  if (!rows.length) {
    return <Typography.Text type="secondary">Категорий пока нет — нажмите «Обновить метатеги»</Typography.Text>
  }

  return (
    <>
      <Table<TreeRow>
        rowKey="id"
        size="small"
        dataSource={tree}
        pagination={false}
        columns={[
          {
            title: 'Категория', dataIndex: 'name',
            render: (name: string, r) => (
              <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => setOpenId(r.id)}>
                {name}
              </Button>
            ),
          },
          { title: 'Статус', width: 240, render: (_, r) => <StatusTag row={r} /> },
          {
            title: 'Обновлено', width: 150,
            render: (_, r) => r.status === 'done' ? dayjs(r.updated_at).format('DD.MM.YYYY HH:mm') : '—',
          },
        ]}
      />
      <CategoryDrawer row={rows.find(r => r.id === openId) ?? null}
                      onClose={() => setOpenId(null)} onChanged={load} />
    </>
  )
}
```

- [ ] **Step 4: `src/pages/CategoryMetaPage.tsx`**

```tsx
import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import {
  Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, SyncOutlined } from '@ant-design/icons'
import {
  MetaProject, MetaProjectIn, SiteBrief, WordstatRegion, createMetaProject, deleteMetaProject,
  getMetaProjects, getSites, runMetaProject, searchWordstatRegions, updateMetaProject,
} from '../api'
import CategoryMetaTree from './CategoryMetaTree'

const isRunning = (p: MetaProject) => Boolean(p.run && !p.run.stale)

function ProjectState({ project }: { project: MetaProject }) {
  const { run } = project
  if (run && run.stale) {
    return <Tag color="error">Обновление оборвалось — запустите заново</Tag>
  }
  if (run) {
    const waiting = run.wait_until && dayjs(run.wait_until).isAfter(dayjs())
    return (
      <Space direction="vertical" size={2}>
        <Tag color="processing">
          {run.total ? `В работе: ${run.done + run.failed} из ${run.total}` : 'Готовим список категорий'}
        </Tag>
        {waiting && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            ждём квоту Wordstat, продолжим ~в {dayjs(run.wait_until).format('HH:mm')}
          </Typography.Text>
        )}
      </Space>
    )
  }
  return (
    <Space direction="vertical" size={2}>
      <Typography.Text>
        {project.updated_at
          ? `Теги обновлены ${dayjs(project.updated_at).format('DD.MM.YYYY HH:mm')}`
          : 'Ещё не обновлялись'}
      </Typography.Text>
      {project.last_error && (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          Последний запуск: {project.last_error}
        </Typography.Text>
      )}
    </Space>
  )
}

function ProjectModal({ project, sites, usedSiteIds, onClose, onSaved }: {
  project: MetaProject | null
  sites: SiteBrief[]
  usedSiteIds: number[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm<MetaProjectIn>()
  const [regions, setRegions] = useState<WordstatRegion[]>([])
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (project) {
      form.setFieldsValue({
        site_id: project.site_id, city: project.city, city_in: project.city_in,
        brand: project.brand, wordstat_region_id: project.wordstat_region_id ?? undefined,
      })
      if (project.wordstat_region_id) {
        setRegions([{ id: project.wordstat_region_id, label: project.city,
                      path: `регион ${project.wordstat_region_id}` }])
      }
    }
  }, [project])

  const findRegions = async () => {
    const city = String(form.getFieldValue('city') || '').trim()
    if (!city) { message.warning('Сначала укажите город'); return }
    setSearching(true)
    try {
      const found = await searchWordstatRegions(city)
      setRegions(found)
      const exact = found.filter(r => r.label.toLowerCase() === city.toLowerCase())
      if (exact.length === 1) form.setFieldValue('wordstat_region_id', exact[0].id)
      if (!found.length) message.warning('Такого города в регионах Wordstat нет')
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setSearching(false) }
  }

  const submit = async (values: MetaProjectIn) => {
    setSaving(true)
    try {
      const payload = { ...values, city_in: values.city_in || '' }
      if (project) await updateMetaProject(project.site_id, payload)
      else await createMetaProject(payload)
      message.success('Проект сохранён')
      onSaved()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setSaving(false) }
  }

  const siteOptions = sites
    .filter(s => project ? s.id === project.site_id : !usedSiteIds.includes(s.id))
    .map(s => ({ value: s.id, label: `${s.name} (${s.domain})` }))

  return (
    <Modal open title={project ? `Проект — ${project.name}` : 'Новый проект'} okText="Сохранить"
           cancelText="Отмена" confirmLoading={saving} onCancel={onClose}
           onOk={() => form.submit()} destroyOnClose>
      <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
        <Form.Item name="site_id" label="Сайт" rules={[{ required: true, message: 'Выберите сайт' }]}>
          <Select disabled={Boolean(project)} options={siteOptions} placeholder="Сайт из раздела «Сайты»" />
        </Form.Item>
        <Form.Item label="Город" required>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="city" noStyle rules={[{ required: true, message: 'Укажите город' }]}>
              <Input placeholder="Москва" />
            </Form.Item>
            <Button onClick={findRegions} loading={searching}>Найти регион</Button>
          </Space.Compact>
        </Form.Item>
        <Form.Item name="wordstat_region_id" label="Регион Wordstat"
                   rules={[{ required: true, message: 'Найдите и выберите регион' }]}
                   extra="Статистика запросов берётся по этому региону">
          <Select placeholder="Нажмите «Найти регион»"
                  options={regions.map(r => ({ value: r.id, label: `${r.label} — ${r.path}` }))} />
        </Form.Item>
        <Form.Item name="city_in" label="Город с предлогом"
                   extra="«в Москве», «во Владимире». Оставьте пустым — определится автоматически">
          <Input placeholder="в Москве" />
        </Form.Item>
        <Form.Item name="brand" label="Бренд в конце title"
                   rules={[{ required: true, message: 'Укажите бренд' }]}>
          <Input placeholder="Стройбаза" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function CategoryMetaPage() {
  const [projects, setProjects] = useState<MetaProject[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [editing, setEditing] = useState<MetaProject | 'new' | null>(null)
  const [busySite, setBusySite] = useState<number | null>(null)

  const load = () => getMetaProjects().then(setProjects)

  useEffect(() => { load(); getSites().then(setSites) }, [])

  useEffect(() => {
    if (!projects.some(isRunning)) return
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [projects])

  const run = async (siteId: number) => {
    setBusySite(siteId)
    try {
      await runMetaProject(siteId)
      message.success('Обновление запущено — идёт в фоне')
      await load()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setBusySite(null) }
  }

  const remove = async (siteId: number) => {
    try { await deleteMetaProject(siteId); await load() }
    catch { /* сообщение уже показал интерцептор */ }
  }

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>Метатеги категорий</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>
          Добавить проект
        </Button>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="site_id"
          dataSource={projects}
          pagination={false}
          scroll={{ x: 720 }}
          locale={{ emptyText: 'Проектов пока нет — добавьте сайт' }}
          columns={[
            {
              title: 'Сайт',
              render: (_, p: MetaProject) => (
                <Space direction="vertical" size={0}>
                  <Typography.Text strong>{p.name}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.domain}</Typography.Text>
                </Space>
              ),
            },
            { title: 'Город', dataIndex: 'city', width: 140 },
            { title: 'Состояние', width: 280, render: (_, p: MetaProject) => <ProjectState project={p} /> },
            {
              title: '', width: 260,
              render: (_, p: MetaProject) => (
                <Space>
                  <Popconfirm
                    title="Обновить метатеги всех категорий?"
                    description="Теги перезапишутся на сайте, в том числе заполненные вручную."
                    onConfirm={() => run(p.site_id)} disabled={isRunning(p)}>
                    <Button icon={<SyncOutlined />} loading={busySite === p.site_id}
                            disabled={isRunning(p)}>
                      Обновить метатеги
                    </Button>
                  </Popconfirm>
                  <Button type="text" icon={<EditOutlined />} onClick={() => setEditing(p)} />
                  <Popconfirm title="Убрать проект из раздела? Теги на сайте останутся."
                              onConfirm={() => remove(p.site_id)}>
                    <Button type="text" icon={<DeleteOutlined />} disabled={isRunning(p)} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
          expandable={{
            expandedRowRender: (p: MetaProject) => (
              <CategoryMetaTree siteId={p.site_id} active={isRunning(p)} />
            ),
          }}
        />
      </Card>

      {editing && (
        <ProjectModal
          project={editing === 'new' ? null : editing}
          sites={sites}
          usedSiteIds={projects.map(p => p.site_id)}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load() }}
        />
      )}
    </>
  )
}
```

- [ ] **Step 5: Меню и маршрут в `src/App.tsx`**

- в импорт иконок добавить `TagsOutlined` (строка `CloseOutlined, UserOutlined, NotificationOutlined, TagsOutlined,`);
- после `import BuilderBatchPage from './pages/BuilderBatchPage'` — `import CategoryMetaPage from './pages/CategoryMetaPage'`;
- в `navItems` после «Строители»: `{ key: '/category-meta', label: 'Метатеги категорий', icon: <TagsOutlined />, admin: false },`
- в `<Routes>` после `/builders/:id`: `<Route path="/category-meta" element={<CategoryMetaPage />} />`

- [ ] **Step 6: Сборка**

Run: `docker compose run --rm frontend sh -c "npm ci && npm run build"`
Expected: `✓ built in …` без ошибок TypeScript (предупреждение о размере чанка > 500 kB было и раньше).

- [ ] **Step 7: Commit**

```bash
git add execution/frontend/src/api.ts execution/frontend/src/statuses.ts execution/frontend/src/App.tsx execution/frontend/src/pages/CategoryMetaPage.tsx execution/frontend/src/pages/CategoryMetaTree.tsx
git commit -m "feat: экран «Метатеги категорий» — проекты, дерево, карточка категории"
```

---

### Task 17: Фронтенд — настройки, промпты, «Доработки»

**Files:**
- Modify: `execution/frontend/src/pages/AdminSettingsPage.tsx`, `src/pages/AdminPromptsPage.tsx`, `src/changelog.ts`

- [ ] **Step 1: `AdminSettingsPage.tsx`**

- заголовок `Настройки RouterAI` → `Настройки`;
- в обоих `form.setFieldsValue({ ...values, routerai_api_key: '' })` и `form.setFieldsValue({ ...saved, routerai_api_key: '' })` добавить `wordstat_api_key: ''` — иначе в поле пароля приедет маска и уйдёт обратно как «новый ключ»;
- после `<Form.Item name="llm_max_retries" …>…</Form.Item>`:

```tsx
          <Typography.Title level={5}>Wordstat — метатеги категорий</Typography.Title>
          <Form.Item name="wordstat_api_key" label="Ключ Yandex Search API"
                     extra="Пусто — оставить текущий ключ">
            <Input.Password placeholder="не отображается" />
          </Form.Item>
          <Form.Item name="wordstat_hourly_limit" label="Запросов к Wordstat в час"
                     extra="Квота Яндекса — 100 в час. Меняйте, только если Яндекс поднимет квоту">
            <Input />
          </Form.Item>
          <Form.Item name="meta_stoplist" label="Стоп-лист для метатегов"
                     extra="Через запятую: конкуренты и мусорные фразы — не попадут ни в один тег">
            <Input.TextArea autoSize={{ minRows: 2 }} />
          </Form.Item>
```

- [ ] **Step 2: `AdminPromptsPage.tsx`**

В массив `KEYS` после элемента `content_image`:

```ts
  { key: 'category_seeds', label: 'Метатеги: фразы', vars: { site_name: 'Стройбаза', site_description: 'Интернет-магазин стройматериалов в Москве, доставка по городу', categories: ['46: Листовые материалы / Фанера', '112: Металлопрокат / Уголок металлический'] } },
  { key: 'category_meta', label: 'Метатеги: теги', vars: { site_name: 'Стройбаза', site_description: 'Интернет-магазин стройматериалов в Москве, доставка по городу', category_name: 'Фанера', category_path: 'Листовые материалы / Фанера', form_nominative: 'фанера', form_buy: 'купить фанеру', form_price: 'цена фанеры', chosen_form: 'nominative', sell_word: 'купить', city_in: 'в Москве', brand: 'Стройбаза', total_count: 96275, phrases: ['фанера — 96275', 'фанера купить — 12238', 'фанера цена — 5300'], violations: [] } },
```

- [ ] **Step 3: `changelog.ts`**

Первым элементом `CHANGELOG`:

```ts
  {
    date: '2026-09-16',
    kind: 'feature',
    title: 'Метатеги категорий по статистике Wordstat',
    text: 'Новый раздел «Метатеги категорий». Добавляете сайт, указываете город и бренд — ' +
          'панель собирает дерево категорий каталога, смотрит в Яндекс Wordstat, как товары ' +
          'ищут в этом городе, и записывает на сайт title, h1, description, keywords и ' +
          'ai_keywords. Форма названия («фанера» или «фанеру») выбирается по тому, как ' +
          'чаще ищут. Wordstat отвечает не больше 100 раз в час, поэтому сайт на 70–80 ' +
          'категорий обновляется около 2,5 часа — прогресс виден в строке проекта. Теги ' +
          'любой категории можно посмотреть и перегенерировать.',
  },
```

Дату поправить на день фактического деплоя.

- [ ] **Step 4: Сборка**

Run: `docker compose run --rm frontend sh -c "npm ci && npm run build"`
Expected: сборка без ошибок.

- [ ] **Step 5: Commit**

```bash
git add execution/frontend/src/pages/AdminSettingsPage.tsx execution/frontend/src/pages/AdminPromptsPage.tsx execution/frontend/src/changelog.ts
git commit -m "feat: настройки Wordstat, промпты метатегов и запись в «Доработки»"
```

---

### Task 18: Полная проверка ветки

- [ ] **Step 1: Регресс бэкенда**

Run: `docker compose run --rm --no-deps backend pytest -q`
Expected: PASS (778 passed, 0 failed).

- [ ] **Step 2: Миграция с нуля на Postgres**

```bash
docker compose up -d postgres
docker compose run --rm backend sh -c "alembic upgrade head && alembic check"
```

Expected: `No new upgrade operations detected.`

- [ ] **Step 3: Сборка фронтенда**

Run: `docker compose run --rm frontend sh -c "npm ci && npm run build"`
Expected: без ошибок.

---

### Task 19: Живая проверка на стенде — «Фанера», stroybaza-moscow.ru

Скилл: `running-local-panel` (поднять стенд, администратор, сессия). Wordstat и RouterAI — настоящие.

**⚠️ Запись на боевой сайт.** Шаги до Step 5 ничего на сайт не пишут. Step 5 перезаписывает метатег категории «Фанера» на stroybaza-moscow.ru — **выполнять только после явного подтверждения владельца** в диалоге. Воркер до этого шага держать остановленным (`docker compose stop worker`), иначе поставленная задача уйдёт на сайт сама.

- [ ] **Step 1: Стенд и настройки**

Поднять `api` и `frontend` (без воркера). В «Настройках» ввести **новый** ключ Wordstat (ключ из переписки 2026-09-16 владелец перевыпускает) и ключ RouterAI. В «Сайтах» — stroybaza-moscow.ru с токеном из `.env` (`SITE_API_TOKEN_stroybaza-moscow`), если его ещё нет.

- [ ] **Step 2: Проект**

«Метатеги категорий» → «Добавить проект»: сайт stroybaza-moscow.ru, город «Москва», «Найти регион» → выбрать 213, город с предлогом — пусто, бренд «Стройбаза». Ожидается: проект сохранён, «город с предлогом» = «в Москве» (определила LLM).

- [ ] **Step 3: Синхронизация и фразы без записи на сайт**

Кнопку «Обновить метатеги» здесь НЕ нажимать: задача ляжет в очередь Redis и при
старте воркера прогонит и перезапишет весь каталог. Запуск создаётся вручную, без
очереди:

```bash
docker compose run --rm -T backend python - <<'PY'
from app.db import SessionLocal
from app.models.category_meta import MetaRun
from app.models.site import Site
from app.tasks import start_meta_run_sync
db = SessionLocal()
site = db.query(Site).filter_by(domain="stroybaza-moscow.ru").one()
run = MetaRun(site_id=site.id)
db.add(run)
db.commit()
print("first:", start_meta_run_sync(db, run.id))
PY
```

Ожидается в дереве проекта: ~70 категорий «В очереди», 7 «Пропущена» (3 не
опубликованы, 4 без страницы), у «Фанера» фраза «фанера», формы «купить
фанеру»/«цена фанеры». В строке проекта — «В работе: 0 из ~70».

- [ ] **Step 4: Теги «Фанеры» без записи**

Сгенерировать теги, подменив клиент сайта заглушкой, которая ничего не пишет:

```bash
docker compose run --rm -T backend python - <<'PY'
from types import SimpleNamespace
from app.ai.factory import build_text_client
from app.category_meta.generator import collect_facts, generate_tags
from app.db import SessionLocal
from app.models.category_meta import CategoryMeta
from app.models.site import Site
from app.wordstat.factory import build_wordstat_client, hourly_limit, stoplist
db = SessionLocal()
row = db.query(CategoryMeta).filter_by(slug="fanera").one()
site = db.get(Site, row.site_id)
facts = collect_facts(db, row, site.wordstat_region_id, lambda: build_wordstat_client(db), hourly_limit(db))
print(facts.chosen_form, facts.nominative_count, facts.declined_count, facts.total_count)
tags = generate_tags(db, row, site, facts, build_text_client(db), stoplist(db), lambda *a: None)
for key, value in tags.items():
    print(f"{key} ({len(value)}): {value}")
PY
```

Ожидается: `nominative 678 234 96275` (числа могут сдвинуться — статистика за 30 дней), пять тегов, прошедших проверку. **Показать теги владельцу.**

- [ ] **Step 5: Запись на сайт — только после подтверждения владельца**

Вернуть все категории из очереди в «new» (чтобы воркер не пошёл по каталогу),
очистить очередь Celery от всего, что могло туда попасть, поднять воркер свежим
кодом:

```bash
docker compose run --rm -T backend python - <<'PY'
from app.db import SessionLocal
from app.models.category_meta import CategoryMeta
from app.models.site import Site
db = SessionLocal()
site = db.query(Site).filter_by(domain="stroybaza-moscow.ru").one()
for row in db.query(CategoryMeta).filter_by(site_id=site.id, status="queued"):
    row.status = "new"
db.commit()
PY
docker compose up -d redis
docker compose run --rm backend celery -A app.celery_app purge -f
docker compose up -d --build worker
```

В карточке «Фанеры» нажать «Перегенерировать». Ожидается: статус «Готово», в
карточке — теги и «Было на сайте до нас»; запуск проекта закрылся («Теги
обновлены …»). На сайте:

```bash
curl -sL -A Mozilla https://stroybaza-moscow.ru/catalog/category/listovye-materialy/fanera/ | grep -E "<title>|<h1|name=\"description\"|name=\"keywords\""
```

— новые title, h1, description, keywords.

- [ ] **Step 6: Итог**

Остановить воркер (`docker compose stop worker`), если полный прогон каталога не согласован. Отчитаться владельцу: теги «Фанеры», как выглядят на сайте, что осталось (деплой по `DEPLOY.md`, полный прогон ~2,5 часа).
