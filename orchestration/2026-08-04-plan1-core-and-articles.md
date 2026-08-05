# План 1: Каркас сервиса + раздел «Статьи» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Веб-сервис на VPS, в котором 2–3 сотрудника создают статьи-черновики на 10+ сайтах: авторизация с ролями, карточки сайтов с шифрованными токенами, редактируемые промпты, генерация тем/текстов/картинок через RouterAI, публикация черновиком.

**Architecture:** Backend — синхронный FastAPI в одном образе с Celery-воркером, устройство скопировано из отлаженного `inntec-inbox` (auth, Fernet-секреты, compose с разовым `migrate`). Frontend — React + Vite + antd, тема и layout из `nst-tg-monitor` с исходным акцентом `#dca34c`. Существующие `filemanager.py` и `gen_images.py` переносятся в бэкенд как модули с параметрами из БД вместо `.env`.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 (sync), Alembic, PostgreSQL 16, Celery 5.4 + Redis, python-jose, bcrypt, cryptography (Fernet), Jinja2 (SandboxedEnvironment), openai SDK (текст через RouterAI), Pillow, pytest + TestClient; React 18, Vite 5, TypeScript 5, antd 5, axios, dayjs.

**Спека:** `directions/2026-08-04-content-service-design.md`
**Образцы для копирования:** `../inntec-inbox/execution/` (backend), `../nst-tg-monitor/frontend/` (тема)

---

## Структура файлов

```
execution/backend/
  requirements.txt              Create
  Dockerfile                    Create
  alembic.ini, alembic/         Create
  pytest.ini                    Create
  create_admin.py               Create: разовый скрипт первого админа
  app/
    config.py                   Create: env-настройки (DB, Redis, JWT, ENCRYPTION_KEY)
    db.py                       Create: Base, engine, SessionLocal
    clock.py                    Create: utcnow()
    main.py                     Create: сборка FastAPI
    celery_app.py               Create: объект Celery
    seed.py                     Create: дефолтные промпты и настройки
    models/
      user.py, setting.py, site.py, prompt_template.py
      article.py                ArticleBatch, Article, ArticleImage
      job.py                    JobRun, LlmUsage
    settings/
      crypto.py                 Create: encrypt/decrypt/mask (порт из образца)
      service.py                Create: SettingsService (порт из образца)
    ai/
      text.py                   Create: OpenAI-совместимый клиент RouterAI
      images.py                 Create: генерация картинок + кроп + webp
      watermark.py              Create: наложение водяного знака (Pillow)
      prompts.py                Create: разрешение site→global + рендер Jinja2
    sites/
      client.py                 Create: клиент API сайта (staticpages, файлы, обложка)
      reference.py              Create: синхронизация раздела и эталонной статьи
    articles/
      topics.py                 Create: генерация тем + дедуп
      builder.py                Create: сборка одной статьи
    api/
      deps.py, security.py, auth.py
      sites.py, admin_sites.py, admin_users.py, admin_settings.py, admin_prompts.py
      article_batches.py, articles.py, jobs.py, tasks_status.py
    tasks.py                    Create: Celery-задачи
  tests/                        Create: по одному файлу на модуль

execution/frontend/
  package.json, tsconfig*.json, vite.config.ts, index.html, Dockerfile, nginx.conf
  src/
    main.tsx, App.tsx, index.css, api.ts, auth.tsx
    pages/
      LoginPage.tsx, ArticlesPage.tsx, BatchPage.tsx, JobsPage.tsx
      AdminSitesPage.tsx, AdminPromptsPage.tsx, AdminSettingsPage.tsx, AdminUsersPage.tsx

execution/docker-compose.yml, docker-compose.prod.yml, .env.prod.example
DEPLOY.md
```

Один роутер — одна предметная область; Pydantic-схемы живут прямо в файле роутера.

## Как запускать

```bash
cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/execution
docker compose run --rm --no-deps backend pytest -q          # тесты без БД
docker compose up -d postgres redis                          # для тестов с БД
docker compose up api frontend                               # локальная разработка
```

Фронтенд проверяется вручную через дев-сервер — модульных тестов на фронте нет, как в обоих образцах.

---

## Фаза 0 — Каркас бэкенда

### Task 1: Скелет проекта и подключение к БД

**Files:**
- Create: `execution/backend/requirements.txt`
- Create: `execution/backend/Dockerfile`
- Create: `execution/backend/.dockerignore`
- Create: `execution/backend/pytest.ini`
- Create: `execution/backend/app/__init__.py`
- Create: `execution/backend/app/config.py`
- Create: `execution/backend/app/db.py`
- Create: `execution/backend/app/clock.py`
- Create: `execution/docker-compose.yml`
- Test: `execution/backend/tests/test_config.py`

- [x] **Step 1: Зависимости**

`execution/backend/requirements.txt`:

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy==2.0.36
alembic==1.14.0
psycopg[binary]==3.2.3
celery[redis]==5.4.0
redis==5.2.1
pydantic==2.10.4
pydantic-settings==2.7.0
cryptography==44.0.0
jinja2==3.1.5
openai==1.59.6
Pillow==11.0.0
requests==2.32.3
beautifulsoup4==4.12.3
openpyxl==3.1.5
httpx==0.28.1
python-jose[cryptography]==3.3.0
bcrypt==4.2.1
python-multipart==0.0.20
pytest==8.3.4
pytest-cov==6.0.0
```

`bcrypt` подключается напрямую, не через `passlib`: `passlib==1.7.4` читает
`bcrypt.__about__.__version__`, которого в `bcrypt>=4.1` уже нет. `openpyxl` и
`beautifulsoup4` понадобятся плану 2 (импорт xlsx и скрейпинг) — ставятся сразу,
чтобы образ не пересобирался повторно.

- [x] **Step 2: Dockerfile**

`execution/backend/Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`execution/backend/.dockerignore` — `COPY . .` иначе запекает в образ `.env` и локальные кэши:

```
.env*
__pycache__/
*.pyc
.pytest_cache/
media/
```

- [x] **Step 3: pytest.ini**

`execution/backend/pytest.ini`:

```ini
[pytest]
testpaths = tests
pythonpath = .
```

- [x] **Step 4: Написать падающий тест**

`execution/backend/tests/test_config.py`:

```python
from app.config import Config


def test_defaults_are_dev_friendly(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    cfg = Config(_env_file=None)
    assert cfg.database_url.startswith("postgresql+psycopg://")
    assert cfg.redis_url.startswith("redis://")
    assert cfg.cookie_secure is False


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "from-env")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    cfg = Config()
    assert cfg.jwt_secret == "from-env"
    assert cfg.cookie_secure is True
```

Тест дефолтов бежит внутри сервиса `backend`, где compose уже выставляет
`DATABASE_URL`/`REDIS_URL` — без `delenv` и `_env_file=None` он молча проверял
бы значения из окружения, а не дефолты класса.

- [x] **Step 5: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose build backend && docker compose run --rm --no-deps backend pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [x] **Step 6: Реализация config.py**

`execution/backend/app/__init__.py` — пустой файл.

`execution/backend/app/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    database_url: str = "postgresql+psycopg://app:app@postgres:5432/content"
    redis_url: str = "redis://redis:6379/0"

    # Инфраструктурные секреты живут в окружении, а не в БД: их ротация не должна
    # зависеть от доступности БД при старте, а ENCRYPTION_KEY ещё и нужен, чтобы
    # прочитать саму таблицу настроек.
    jwt_secret: str = ""
    encryption_key: str = ""
    cookie_secure: bool = False

    media_dir: str = "/app/media"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


config = Config()
```

`extra="ignore"` обязателен: дефолт pydantic-settings 2.7 — `extra="forbid"`, и
любой лишний ключ в `.env` роняет `ValidationError` прямо на импорте модуля
(`config = Config()` на уровне модуля падает у api, worker, migrate и тестов
разом).

- [x] **Step 7: clock.py и db.py**

`execution/backend/app/clock.py`:

```python
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Единая точка получения времени — подменяется в тестах."""
    return datetime.now(timezone.utc)
```

`execution/backend/app/db.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import config


class Base(DeclarativeBase):
    pass


engine = create_engine(config.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
```

- [x] **Step 8: docker-compose.yml**

`execution/docker-compose.yml`:

```yaml
x-backend-base: &backend-base
  build: ./backend
  volumes:
    - ./backend:/app
    - media:/app/media
  environment:
    DATABASE_URL: postgresql+psycopg://app:${DB_PASSWORD:-app}@postgres:5432/content
    REDIS_URL: redis://redis:6379/0
    JWT_SECRET: ${JWT_SECRET:-dev-jwt-secret-change-in-prod}
    ENCRYPTION_KEY: ${ENCRYPTION_KEY:-8Bq3mA0kXqL2pR7vT1yZ4nC6wE9sU5hJ0dF2gK8lM3o=}
    MEDIA_DIR: /app/media
    TZ: Europe/Samara

services:
  backend:
    <<: *backend-base
    profiles: ["tools"]
    depends_on:
      postgres:
        condition: service_healthy

  # Разовый шаг миграций, а не «каждый сервис мигрирует сам»: api и worker
  # стартуют одновременно, и два `alembic upgrade head` гонялись бы за одной
  # DDL-транзакцией на пустой базе.
  migrate:
    <<: *backend-base
    command: alembic upgrade head
    depends_on:
      postgres:
        condition: service_healthy

  api:
    <<: *backend-base
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    ports: ["127.0.0.1:8000:8000"]
    depends_on:
      redis:
        condition: service_started
      postgres:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully

  worker:
    <<: *backend-base
    command: celery -A app.celery_app worker --loglevel=info --concurrency=2
    depends_on:
      redis:
        condition: service_started
      postgres:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully

  frontend:
    image: node:20-alpine
    working_dir: /app
    command: sh -c "npm install && npm run dev -- --host"
    volumes:
      - ./frontend:/app
    ports: ["127.0.0.1:3000:3000"]
    depends_on:
      - api

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: content
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${DB_PASSWORD:-app}
    ports: ["127.0.0.1:5432:5432"]
    volumes: [postgres_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d content"]
      interval: 10s

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes: [redis_data:/data]

volumes:
  postgres_data:
  redis_data:
  media:
```

Четыре точечные правки против первой версии: `DATABASE_URL` берёт пароль из
`${DB_PASSWORD:-app}` (иначе смена `DB_PASSWORD` меняет пароль только у
postgres, а строка подключения остаётся на `app:app`); порты `api`/`postgres`/
`frontend` привязаны к `127.0.0.1`, а не ко всем интерфейсам хоста (иначе
postgres с паролем `app` торчит наружу); якорь `&backend-env` убран как
неиспользуемый (нигде не разыменовывался); у сервиса `backend` появился
`profiles: ["tools"]` — без своего `command` он иначе наследует CMD из
Dockerfile и в обычном `docker compose up` поднимает второй `uvicorn` без
портов и без смысла. Сервис `backend` остаётся только для разовых команд
(`docker compose run --rm --no-deps backend pytest ...`).

- [x] **Step 9: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose build backend && docker compose run --rm --no-deps backend pytest tests/test_config.py -v`
Expected: PASS — 2 passed

- [x] **Step 10: Commit**

```bash
git add execution/backend execution/docker-compose.yml
git commit -m "feat: скелет бэкенда — config, db, compose"
```

---

### Task 2: Alembic и первая миграция (таблица users)

**Files:**
- Create: `execution/backend/alembic.ini`
- Create: `execution/backend/alembic/env.py`
- Create: `execution/backend/alembic/script.py.mako`
- Create: `execution/backend/alembic/README` (генерируется `alembic init`, не редактируется)
- Create: `execution/backend/app/models/__init__.py` — агрегатор моделей, не пустой файл
- Create: `execution/backend/app/models/user.py`
- Create: `execution/backend/tests/conftest.py` — фикстура `db_session` (in-memory SQLite)
- Create: `execution/backend/alembic/versions/884f54cd83b6_users.py` (генерируется `alembic revision --autogenerate`)
- Test: `execution/backend/tests/test_models_user.py`

- [x] **Step 1: Написать падающий тест**

Python-дефолты (`default=...`) в SQLAlchemy применяются на INSERT, а не в
`__init__` — значит, чтобы тест реально проверял значение, а не `None`,
нужна сессия с БД уже на этом шаге. `execution/backend/tests/conftest.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
import app.models  # noqa: F401 — регистрирует все модели в Base.metadata

# SQLite в памяти: модельные и (позже) API-тесты проверяют поведение, а не
# диалект БД. Postgres-специфичного SQL в моделях нет.
TEST_URL = "sqlite:///:memory:"


@pytest.fixture
def db_session():
    # poolclass=StaticPool обязателен: FastAPI выполняет синхронные эндпоинты
    # в отдельном потоке (run_in_threadpool), а sqlite3 для ":memory:" по
    # умолчанию заводит каждому потоку свою независимую базу — эндпоинт увидел
    # бы пустую БД без таблиц, хотя фикстура создала их в потоке теста.
    # StaticPool держит одно соединение на все потоки.
    engine = create_engine(
        TEST_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
```

`execution/backend/tests/test_models_user.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.user import User


def test_user_defaults(db_session):
    # Дефолты SQLAlchemy применяются на INSERT, а не в __init__ — поэтому
    # значения проверяются после flush(), иначе role/is_active всегда None
    # и assert проходит независимо от того, что реально задано в модели.
    user = User(email="a@b.ru", full_name="Иван", password_hash="x")
    db_session.add(user)
    db_session.flush()

    assert user.role == "manager"
    assert user.is_active is True
    assert User.__tablename__ == "users"


def test_role_column_allows_admin(db_session):
    user = User(email="a@b.ru", full_name="Иван", password_hash="x", role="admin")
    db_session.add(user)
    db_session.flush()

    assert user.role == "admin"


def test_email_is_unique(db_session):
    db_session.add(User(email="dup@b.ru", full_name="Иван", password_hash="x"))
    db_session.commit()

    db_session.add(User(email="dup@b.ru", full_name="Пётр", password_hash="y"))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

Ранняя версия теста сравнивала `user.role is None or user.role == "manager"` без
`flush()` — при отсутствующей сессии первая ветвь истинна всегда, и тест не ловил
ни один из пяти проверенных мутационным тестированием дефектов модели (смена
дефолта, потеря `unique=True`, инверсия `is_active` и т.д.). Нынешняя версия
проверяет фактические значения после `flush()/commit()` и реальное поведение
UNIQUE-констрейнта через `IntegrityError`.

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_user.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'` (всплывает
уже на загрузке `conftest.py`, который делает `import app.models`, поэтому
pytest репортит `ImportError while loading conftest`, а не ошибку в самом
тестовом файле)

- [x] **Step 3: Модель User**

`execution/backend/app/models/__init__.py` — агрегатор моделей:

```python
from app.models.user import User

# Единая точка регистрации моделей: alembic/env.py и tests/conftest.py делают
# `import app.models`, чтобы Base.metadata увидел все таблицы разом. Новую
# модель — добавляй сюда, а не в env.py/conftest.py по отдельности.
__all__ = ["User"]
```

Реестр моделей живёт в одном месте, а не размазан по `env.py` и `conftest.py`
списком импортов: забытый импорт одной модели в двух местах расходится по-разному
и ломает автогенерацию или тестовую схему по-разному, далеко от причины.

`execution/backend/app/models/user.py`:

```python
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(300))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="manager")  # admin | manager
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

- [x] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_user.py -v`
Expected: PASS — 3 passed

- [x] **Step 5: Инициализировать Alembic**

Run: `cd execution && docker compose run --rm --no-deps backend alembic init alembic`

Затем в `execution/backend/alembic.ini` заменить строку `sqlalchemy.url = ...` на пустую:

```ini
sqlalchemy.url =
```

URL берётся из окружения в `env.py` — держать пароль БД в файле конфигурации незачем.

- [x] **Step 6: Настроить alembic/env.py**

Заменить содержимое `execution/backend/alembic/env.py` на:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

# Логирование настраивается до импорта app.*: fileConfig(disable_existing_loggers=True)
# по умолчанию глушит все уже созданные логгеры — если бы app.config/app.db успели
# завести свои до этого вызова, они замолчали бы молча и без предупреждения.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.config import config as app_config
from app.db import Base
import app.models  # noqa: F401 — регистрирует все модели в Base.metadata

# `%` в пароле БД (типичный символ в base64-паролях) иначе интерпретируется
# ConfigParser'ом как начало интерполяции и роняет любую команду alembic ещё
# до обращения к БД — экранируем его перед записью в config.
config.set_main_option("sqlalchemy.url", app_config.database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Три вещи в этом файле не очевидны сразу:

- `fileConfig()` вызывается ДО импорта `app.config`/`app.db`, а не после. У
  `fileConfig()` по умолчанию `disable_existing_loggers=True` — если бы модули
  приложения успели завести свои логгеры раньше, вызов молча заглушил бы их.
  Сейчас `app.*` логгеров на импорте не заводит, поэтому эффекта нет, но это
  отложенные грабли: порядок из штатного шаблона `alembic init` соблюдён,
  чтобы их не встретить в будущей задаче.
- `import app.models` — одна строка вместо перечисления моделей поимённо.
  `autogenerate` видит только те таблицы, чьи классы зарегистрированы в
  `Base.metadata` к моменту запуска, но носителем списка моделей выступает
  `app/models/__init__.py` (см. Step 3), а не `env.py`: там модель добавляется
  один раз и видна сразу и alembic'у, и `tests/conftest.py`.
- `app_config.database_url.replace("%", "%%")` — `config.set_main_option()`
  кладёт значение в `ConfigParser`, где одиночный `%` означает начало
  интерполяции. Пароль вида `p%40ss` (типичный результат `openssl rand
  -base64`, если в нём встретился `%`) роняет любую команду alembic ещё до
  обращения к БД: `ValueError: invalid interpolation syntax in '...'`.
  Экранирование `%` → `%%` — стандартный способ ConfigParser'а вставить
  литеральный `%`.

- [x] **Step 7: Сгенерировать миграцию**

Run:
```bash
cd execution && docker compose up -d postgres && sleep 5
docker compose run --rm backend alembic revision --autogenerate -m "users"
docker compose run --rm backend alembic upgrade head
```
Expected: создан файл в `alembic/versions/`, вывод `Running upgrade -> <hash>, users`

- [x] **Step 8: Commit**

```bash
git add execution/backend/alembic execution/backend/alembic.ini execution/backend/app/models \
  execution/backend/tests/test_models_user.py execution/backend/tests/conftest.py \
  orchestration/2026-08-04-plan1-core-and-articles.md
git commit -m "feat: alembic и таблица users"
```

---

### Task 3: Пароли и JWT

**Files:**
- Create: `execution/backend/app/api/__init__.py`
- Create: `execution/backend/app/api/security.py`
- Test: `execution/backend/tests/test_api_security.py`

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_api_security.py`:

```python
import time
from datetime import timedelta

import pytest
from jose import jwt as jose_jwt

from app.api.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    BCRYPT_MAX_BYTES,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.clock import utcnow


def test_hash_is_not_plaintext():
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert hashed.startswith("$2b$")


def test_verify_accepts_correct_password():
    assert verify_password("secret123", hash_password("secret123")) is True


def test_verify_rejects_wrong_password():
    assert verify_password("wrong", hash_password("secret123")) is False


def test_verify_rejects_empty_hash_without_raising():
    """Пустой password_hash (например, повреждённая строка в БД) должен
    читаться как «пароль не подошёл», а не валить эндпоинт 500-й."""
    assert verify_password("secret123", "") is False


def test_verify_rejects_truncated_hash_without_raising():
    """bcrypt 4.2.1 бросает pyo3_runtime.PanicException на хешах с обрезанной
    солью (длина 8–29 символов после префикса `$2b$12$`) — эта строка длиной
    16 воспроизводит панику. PanicException наследуется от BaseException, а
    не от Exception, поэтому `except Exception` в эндпоинте её не поймает."""
    assert verify_password("secret123", "$2b$12$shortsalt") is False


def test_hash_password_accepts_72_byte_password():
    password = "a" * BCRYPT_MAX_BYTES
    hashed = hash_password(password)
    assert verify_password(password, hashed) is True


def test_hash_password_rejects_password_over_72_bytes():
    with pytest.raises(ValueError):
        hash_password("a" * (BCRYPT_MAX_BYTES + 1))


def test_hash_password_rejects_long_cyrillic_password():
    # Кириллица — 2 байта на символ в UTF-8: 37 символов = 74 байта > лимита.
    with pytest.raises(ValueError):
        hash_password("а" * 37)


def test_token_roundtrip():
    token = create_access_token(user_id=7, role="admin", secret="test-secret")
    payload = decode_access_token(token, secret="test-secret")
    assert payload["user_id"] == 7
    assert payload["role"] == "admin"


def test_token_rejects_wrong_secret():
    from jose import JWTError

    token = create_access_token(user_id=7, role="admin", secret="test-secret")
    with pytest.raises(JWTError):
        decode_access_token(token, secret="other-secret")


def test_expire_is_reasonable():
    """12 часов — рабочая смена. Короче — разлогинит посреди партии статей,
    длиннее — расширяет окно злоупотребления украденной cookie."""
    assert 60 <= ACCESS_TOKEN_EXPIRE_MINUTES <= 24 * 60


def test_exp_claim_is_now_plus_expiry():
    before = utcnow()
    token = create_access_token(user_id=1, role="admin", secret="test-secret")
    payload = decode_access_token(token, secret="test-secret")
    after = utcnow()

    expected_min = before.timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
    expected_max = after.timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert expected_min - 2 <= payload["exp"] <= expected_max + 2


def test_exp_claim_is_correct_regardless_of_local_timezone(monkeypatch):
    """`create_access_token` считает `exp` как `utcnow().timestamp() + ...`.
    Это корректно только пока `utcnow()` возвращает aware-datetime в UTC —
    aware `.timestamp()` даёт правильный эпох независимо от TZ процесса.
    Если `clock.utcnow()` когда-нибудь станет naive (например, ради
    совместимости с naive-колонками БД, как это случилось в другом
    проекте), naive `.timestamp()` начнёт трактовать время как локальное
    для процесса, и exp каждого токена тихо сдвинется на величину TZ. Тест
    держит TZ процесса не-UTC, чтобы такая регрессия не могла спрятаться за
    тем, что контейнер по умолчанию работает в UTC."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    try:
        before = utcnow()
        token = create_access_token(user_id=1, role="admin", secret="test-secret")
        payload = decode_access_token(token, secret="test-secret")
        after = utcnow()

        expected_min = before.timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
        expected_max = after.timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
        assert expected_min - 2 <= payload["exp"] <= expected_max + 2
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_expired_token_is_rejected():
    from jose import ExpiredSignatureError

    past = utcnow() - timedelta(minutes=1)
    token = jose_jwt.encode(
        {"user_id": 1, "role": "admin", "exp": int(past.timestamp())},
        "test-secret",
        algorithm=ALGORITHM,
    )
    with pytest.raises(ExpiredSignatureError):
        decode_access_token(token, secret="test-secret")


def test_create_access_token_rejects_empty_secret():
    with pytest.raises(ValueError):
        create_access_token(user_id=1, role="admin", secret="")


def test_create_access_token_secret_is_keyword_only():
    with pytest.raises(TypeError):
        create_access_token(1, "admin", "test-secret")
```

Изначальная версия (6 тестов выше многоточия убраны, полный список — 16 штук)
не покрывала четыре реальных дефекта, найденных ревью (см. пояснение после
Step 3): необрабатываемое исключение на битом хеше, `exp`, который никогда не
проверялся на реальное значение, отсутствие защиты от пустого секрета и
позиционной путаницы `role`/`secret`, и молчаливую обрезку пароля bcrypt'ом на
72 байтах. Тесты выше добавлены до соответствующих правок в `security.py` и
были красными до них (см. отчёт о мутационной проверке в коммите
`fix: замечания ревью по паролям и JWT`).

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.security'`

- [x] **Step 3: Реализация**

`execution/backend/app/api/__init__.py` — пустой файл.

`execution/backend/app/api/security.py`:

```python
"""Пароли и JWT. Bcrypt используется напрямую (без passlib) — единственная
схема хеширования, лишняя абстракция не нужна.

Отзыв токенов: деактивация пользователя и смена роли действуют немедленно —
`get_current_user`/`require_role` (Task 4) проверяют пользователя в БД на
каждый запрос. Смена пароля живые сессии НЕ убивает — уже выданный токен
остаётся рабочим до истечения `ACCESS_TOKEN_EXPIRE_MINUTES` (до 12 часов).
Для внутренней панели на 2–3 человека это осознанный компромисс, а не
недосмотр: полноценный отзыв (блэклист, `jti`) не реализуем.

Зависимость: `python-jose` не поддерживается с 2021 года и триггерит
`DeprecationWarning` на Python 3.12 (использует `datetime.utcnow()` внутри
`jwt.py`). Известные CVE (алгоритмическая путаница, DoS через JWE) здесь не
эксплуатируются — используется только HS256, без JWE и асимметричных ключей.
Если апгрейд когда-нибудь понадобится, заменой является `pyjwt`.
"""

import bcrypt
from jose import jwt

from app.clock import utcnow

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 12 * 60

# bcrypt молча обрезает ввод на 72 байтах (36 кириллических символов, т.к. в
# UTF-8 это 2 байта на букву) — два пароля с общим 72-байтным префиксом
# становятся взаимозаменяемыми, и смена длинного пароля правкой хвоста тихо
# ни на что не влияет. Валидируем на входе, чтобы это не выяснялось на
# инциденте.
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    if len(password.encode()) > BCRYPT_MAX_BYTES:
        raise ValueError(f"пароль длиннее {BCRYPT_MAX_BYTES} байт")
    # Cost зафиксирован явно (12), а не оставлен на дефолт bcrypt.gensalt():
    # _DUMMY_HASH в app/api/auth.py считается один раз при импорте и должен
    # оставаться неотличимым по времени от хешей реальных пользователей.
    # Если библиотека когда-нибудь сменит дефолтный cost, у уже сохранённых
    # в БД хешей (cost 12) и у свежего dummy-хеша (новый дефолт) разойдётся
    # время bcrypt.checkpw — и тайминг-защита сломается молча.
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    # Битый или пустой хеш в БД — это 401 (не найден/не подходит), а не 500.
    # bcrypt 4.2.1 бросает ValueError("Invalid salt") на явном мусоре и
    # pyo3_runtime.PanicException на хешах с обрезанной солью (длина ~8–29
    # символов после префикса) — вторая наследуется от BaseException, а не
    # от Exception, и её не поймает ни `except Exception` в эндпоинте, ни
    # ServerErrorMiddleware Starlette. Ловим широко и намеренно, но не глушим
    # сигналы прерывания процесса.
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def create_access_token(user_id: int, role: str, *, secret: str) -> str:
    # `secret` — keyword-only: `role` и `secret` — соседние параметры одного
    # типа (str), и при позиционном вызове их перестановка молча подписала бы
    # токен строкой роли вместо секрета, пройдя проверку типов.
    if not secret:
        raise ValueError("secret не может быть пустым")

    # `.timestamp()` даёт корректный UTC-эпох только потому, что
    # `app.clock.utcnow()` возвращает timezone-aware datetime в UTC. Если
    # utcnow() когда-нибудь станет naive (например, ради совместимости с
    # naive-колонками БД), `.timestamp()` начнёт трактовать время как
    # локальное для процесса — и exp каждого токена тихо сдвинется на
    # величину локального TZ.
    expire = utcnow().timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = {
        "user_id": user_id,
        # role в токене — информационная подсказка, не источник авторизации.
        # require_role проверяет роль из БД на каждый запрос: если её когда-
        # нибудь начнут читать отсюда «ради экономии запроса», деактивация
        # или смена роли перестанет действовать немедленно.
        "role": role,
        "exp": int(expire),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=[ALGORITHM])
```

Ревью Task 3 нашло четыре реальных дефекта в первой версии кода и потребовало
правок (коммит `fix: замечания ревью по паролям и JWT`):

- `verify_password` падала необрабатываемым исключением на битом/обрезанном
  bcrypt-хеше. Хуже того, на хешах с обрезанной солью (длина 8–29 символов)
  bcrypt 4.2.1 бросает `pyo3_runtime.PanicException`, которая наследуется от
  `BaseException`, а не от `Exception` — её не поймал бы ни `except
  Exception` в эндпоинте логина (Task 4), ни `ServerErrorMiddleware`
  Starlette. Битый хеш в БД превращался в 500 с трейсбеком вместо 401.
  Обёрнуто в `try/except BaseException` с явным пропуском
  `KeyboardInterrupt`/`SystemExit`.
- Тесты проходили с токенами, у которых `exp` вообще отсутствовал в payload
  или был в неверных единицах — `test_expire_is_reasonable` пиннит константу,
  но ничего не проверяет про сам `create_access_token`. Добавлены
  `test_exp_claim_is_now_plus_expiry` (реальный `exp` = «сейчас» +
  `ACCESS_TOKEN_EXPIRE_MINUTES`) и `test_expired_token_is_rejected`
  (истёкший токен отклоняется `ExpiredSignatureError`). `exp` также приведён
  к `int` — RFC допускает float, но потребители ожидают int.
- `create_access_token(user_id, role, secret)` не защищён от пустого
  `secret` (`config.jwt_secret` по умолчанию `""`, и `jose` спокойно подписал
  бы токен пустой строкой — подделываемый кем угодно) и уязвим к перепутыванию
  соседних `str`-параметров `role`/`secret` при позиционном вызове. `secret`
  сделан keyword-only, добавлена проверка на пустую строку.
- bcrypt молча обрезает пароль на 72 байтах — два пароля с общим 72-байтным
  префиксом становятся взаимозаменяемыми, и смена длинного пароля правкой
  хвоста тихо ни на что не влияет. Добавлена явная проверка длины в
  `hash_password` (`BCRYPT_MAX_BYTES`).

Также задокументирована зависимость `.timestamp()` от `utcnow()`,
возвращающего aware-datetime (иначе `.timestamp()` трактует время как
локальное для процесса), и то, что claim `role` в токене — информационная
подсказка, а не источник авторизации (роль проверяется из БД в
`require_role` на каждый запрос).

- [x] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_security.py -v`
Expected: PASS — 16 passed (после ревью и правок; изначально — 6 passed)

- [x] **Step 5: Commit**

```bash
git add execution/backend/app/api execution/backend/tests/test_api_security.py
git commit -m "feat: хеширование паролей и JWT"
```

---

### Task 4: Авторизация — роутер, зависимости, первый админ

**Files:**
- Create: `execution/backend/app/api/deps.py`
- Create: `execution/backend/app/api/auth.py`
- Create: `execution/backend/app/main.py`
- Create: `execution/backend/create_admin.py`
- Modify: `execution/backend/tests/conftest.py` (дополнить фикстурами для API; `db_session` там уже есть — создан в Task 2)
- Test: `execution/backend/tests/test_api_auth.py`
- Test: `execution/backend/tests/test_api_deps.py` (добавлен при ревью — юнит-тест на `get_db` напрямую, см. Step 1)

- [x] **Step 1: Фикстуры для тестов API**

`tests/conftest.py` уже существует (Task 2) и содержит фикстуру `db_session` на
in-memory SQLite. Здесь он дополняется, а не создаётся заново — `db_session`
берётся оттуда как есть, ниже дописываем в тот же файл:

> **Почему `db_session` в Task 2 создан со `StaticPool`.** Без него тесты
> этого файла падают с `no such table: users`: FastAPI выполняет синхронные
> эндпоинты в отдельном потоке (`run_in_threadpool`), а `sqlite3` для
> `:memory:` по умолчанию даёт каждому потоку свою независимую базу — эндпоинт
> видит пустую БД, хотя фикстура создала таблицы и записала admin/manager в
> потоке теста. Эмпирически: без `StaticPool` — `4 failed, 1 passed, 1 error`,
> с ним — `6 passed`. Обнаружено при исполнении Task 4, устранено в блоке
> Task 2, чтобы дефект не воспроизводился при повторном прогоне плана.

```python
import contextlib

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config as app_config
from app.db import Base
import app.models  # noqa: F401 — регистрирует все модели в Base.metadata

from app.api.deps import get_db, require_role
from app.api.security import hash_password
from app.main import app
from app.models.user import User

# SQLite в памяти: модельные и (позже) API-тесты проверяют поведение, а не
# диалект БД. Postgres-специфичного SQL в моделях нет.
TEST_URL = "sqlite:///:memory:"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    """Без этой фикстуры тесты одалживают JWT_SECRET из окружения процесса
    (docker-compose.yml для запуска через `docker compose run`). Проверено:
    `docker compose run --rm -e JWT_SECRET= backend pytest -q` без этой
    фикстуры валит 5 тестов по причине, не связанной с кодом — секрет пуст,
    `create_access_token` бросает ValueError. Фикстура автоиспользуемая, чтобы
    не дублировать `monkeypatch.setattr` в каждом тестовом файле."""
    monkeypatch.setattr(app_config.config, "jwt_secret", "test-secret")


@pytest.fixture
def db_session():
    # poolclass=StaticPool обязателен: FastAPI выполняет синхронные эндпоинты
    # в отдельном потоке (run_in_threadpool), а sqlite3 для ":memory:" по
    # умолчанию заводит каждому потоку свою независимую базу — эндпоинт увидел
    # бы пустую БД без таблиц, хотя фикстура создала их в потоке теста.
    # StaticPool держит одно соединение на все потоки.
    engine = create_engine(
        TEST_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def _client_for(db_session):
    """Фабрика TestClient на переданной сессии БД. Override `get_db` и все
    открытые клиенты закрываются один раз в конце теста этой фикстурой, а не
    в каждой client-фикстуре по отдельности: admin_client и manager_client
    встречаются в одном тесте, и снятие override в одной из них стирало бы
    её у другой раньше времени."""
    with contextlib.ExitStack() as stack:

        def _make(email: str = "", password: str = "") -> TestClient:
            app.dependency_overrides[get_db] = lambda: db_session
            client = stack.enter_context(TestClient(app))
            if email:
                client.post("/api/auth/login", data={"username": email, "password": password})
            return client

        yield _make
        app.dependency_overrides.clear()


@pytest.fixture
def client(_client_for):
    return _client_for()


@pytest.fixture
def admin(db_session):
    user = User(email="admin@k1.ru", full_name="Админ",
                password_hash=hash_password("adminpass"), role="admin", is_active=True)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def manager(db_session):
    user = User(email="manager@k1.ru", full_name="Менеджер",
                password_hash=hash_password("managerpass"), role="manager", is_active=True)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def admin_client(_client_for, admin):
    return _client_for("admin@k1.ru", "adminpass")


@pytest.fixture
def manager_client(_client_for, manager):
    return _client_for("manager@k1.ru", "managerpass")


@pytest.fixture
def admin_only_route():
    """`require_role` (Task 4) не подключён ни к одному боевому эндпоинту —
    ролевые эндпоинты появятся в Task 6+. Чтобы не ждать до тех пор с
    проверкой самого механизма авторизации, на время теста навешиваем на
    боевой `app` служебный GET-роут за `require_role("admin")`, а после
    теста снимаем его — в проде эндпоинт не остаётся."""
    router = APIRouter()

    @router.get("/_probe/admin-only")
    def admin_only(user: User = Depends(require_role("admin"))):
        return {"email": user.email}

    app.include_router(router)
    yield "/_probe/admin-only"
    app.router.routes[:] = [
        route for route in app.router.routes if getattr(route, "path", None) != "/_probe/admin-only"
    ]
```

> **Дополнение (Task 4, после ревью).** Мутационная проверка при сдаче задачи
> показала: «`require_role` всегда пропускает» и «`get_current_user` не
> проверяет `is_active`» не роняли ни одного теста — `require_role` нигде не
> подключён, а деактивация проверялась только на входе, не на уже выданном
> cookie. `admin_only_route` — временный сервисный роут поверх боевого `app`
> для проверки `require_role` в изоляции, до появления ролевых эндпоинтов в
> Task 6+; добавлен импорт `APIRouter`, `Depends` в `fastapi` и `require_role`
> в `app.api.deps` в начале файла (см. полный текст `conftest.py`).

> **Дополнение 2 (Task 4, ревью качества).** Найдено ещё две дыры:
> 1. Тесты одалживали `JWT_SECRET` из окружения процесса (`docker-compose.yml`)
>    — `docker compose run --rm -e JWT_SECRET= backend pytest -q` валил 5
>    тестов по причине, не связанной с кодом. Добавлена автоиспользуемая
>    `_jwt_secret` (см. код выше) — фиксирует секрет для всех тестов файла
>    вне зависимости от окружения запуска.
> 2. `conftest.py` подменяет `get_db` на `lambda: db_session` для всех
>    API-тестов, поэтому настоящий генератор `get_db` с `try/finally:
>    db.close()` не выполнялся ни в одном тесте — рефакторинг, потерявший
>    `finally`, уехал бы зелёным. Добавлен отдельный файл
>    `tests/test_api_deps.py`, гоняющий сам генератор напрямую (без FastAPI,
>    без реальной БД — `SessionLocal` подменяется на фейковую сессию),
>    проверяет закрытие и при обычном возврате, и при `gen.throw(...)`
>    (так FastAPI закрывает generator-зависимости, если обработка запроса
>    упала с исключением).

- [x] **Step 2: Написать падающий тест**

`execution/backend/tests/test_api_auth.py`:

```python
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app


def test_login_sets_cookie(client, admin):
    resp = client.post("/api/auth/login",
                       data={"username": "admin@k1.ru", "password": "adminpass"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    assert "access_token" in resp.cookies


def test_login_rejects_wrong_password(client, admin):
    resp = client.post("/api/auth/login",
                       data={"username": "admin@k1.ru", "password": "nope"})
    assert resp.status_code == 401


def test_login_hides_whether_user_exists(client, admin):
    """Один и тот же ответ на «нет такого email» и «неверный пароль» — иначе
    форма логина превращается в оракул для перебора адресов."""
    absent = client.post("/api/auth/login",
                         data={"username": "ghost@k1.ru", "password": "nope"})
    wrong = client.post("/api/auth/login",
                        data={"username": "admin@k1.ru", "password": "nope"})
    assert absent.status_code == wrong.status_code == 401
    assert absent.json()["detail"] == wrong.json()["detail"]


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_profile(admin_client):
    resp = admin_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json() == {"email": "admin@k1.ru", "full_name": "Админ", "role": "admin"}


def test_inactive_user_cannot_login(client, db_session, admin):
    admin.is_active = False
    db_session.commit()
    resp = client.post("/api/auth/login",
                       data={"username": "admin@k1.ru", "password": "adminpass"})
    assert resp.status_code == 401


def test_active_session_revoked_on_deactivation(admin_client, db_session, admin):
    """Cookie выдан на 12 часов при логине; если админ деактивирует
    пользователя, следующий же запрос должен получить 401, а не работать
    до истечения токена — ради этого get_current_user проверяет is_active
    в БД на каждый запрос, а не только на входе."""
    admin.is_active = False
    db_session.commit()
    resp = admin_client.get("/api/auth/me")
    assert resp.status_code == 401


def test_require_role_allows_matching_role(admin_client, admin_only_route):
    resp = admin_client.get(admin_only_route)
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@k1.ru"


def test_require_role_rejects_other_role(manager_client, admin_only_route):
    resp = manager_client.get(admin_only_route)
    assert resp.status_code == 403


def test_require_role_rejects_unauthenticated_with_401_not_403(client, admin_only_route):
    """401, а не 403: иначе защищённый роут выдаёт сам факт своего
    существования тому, кто вообще не прошёл аутентификацию."""
    resp = client.get(admin_only_route)
    assert resp.status_code == 401


def test_login_is_case_insensitive_and_trims_whitespace(client, admin):
    """Колонка email в БД регистрозависима (миграция ради этого сейчас
    избыточна), поэтому нормализация — на входе в login(). Без неё админ,
    набравший почту в регистре из своего почтового клиента, не мог бы войти,
    без самовосстановления — только shell в контейнер."""
    resp = client.post("/api/auth/login",
                       data={"username": "  Admin@K1.Ru  ", "password": "adminpass"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@k1.ru"


def test_logout_clears_cookie(admin_client):
    resp = admin_client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert admin_client.get("/api/auth/me").status_code == 401


def test_health_ok_when_db_reachable(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_reports_db_failure(client):
    """DEPLOY.md (Task 26) использует /api/health как единственную дымовую
    проверку после выкладки — она обязана уметь сказать "нет", если БД
    недоступна, а не врать {"status": "ok"} как раньше (статический ответ)."""
    def _broken_db():
        raise RuntimeError("симуляция недоступной БД")
        yield  # pragma: no cover - никогда не достигается

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _broken_db
    try:
        with TestClient(app, raise_server_exceptions=False) as broken:
            resp = broken.get("/api/health")
        assert resp.status_code == 500
    finally:
        if previous_override is not None:
            app.dependency_overrides[get_db] = previous_override
        else:
            app.dependency_overrides.pop(get_db, None)
```

`execution/backend/tests/test_api_deps.py` (новый файл, добавлен при ревью —
см. «Дополнение 2» выше):

```python
"""`conftest.py` подменяет `get_db` на `lambda: db_session` для всех
API-тестов (см. `_client_for`), поэтому настоящий генератор с
`try/finally: db.close()` не выполняется НИ В ОДНОМ тесте `test_api_auth.py`
— рефакторинг, потерявший `finally`, уехал бы в проде зелёным. Эти тесты
гоняют сам генератор `get_db` напрямую, без FastAPI и без реальной БД:
`SessionLocal` подменяется на фабрику фейковой сессии, интересен только факт
вызова `close()`.
"""

import pytest

from app.api import deps


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_get_db_closes_session_after_normal_use(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(deps, "SessionLocal", lambda: session)

    gen = deps.get_db()
    yielded = next(gen)
    assert yielded is session
    assert session.closed is False

    with pytest.raises(StopIteration):
        next(gen)
    assert session.closed is True


def test_get_db_closes_session_when_caller_raises(monkeypatch):
    """FastAPI закрывает generator-зависимости через `gen.throw(...)`, если
    обработка запроса упала с исключением — `finally` обязан сработать и в
    этом случае, а не только при штатном завершении."""
    session = _FakeSession()
    monkeypatch.setattr(deps, "SessionLocal", lambda: session)

    gen = deps.get_db()
    next(gen)
    with pytest.raises(ValueError):
        gen.throw(ValueError("запрос упал"))
    assert session.closed is True
```

- [x] **Step 3: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_auth.py -v`
Expected: FAIL — `ModuleNotFoundError` (фактически первым не найден `app.api.deps`,
т.к. `conftest.py` импортирует его раньше `app.main` — не `app.main`, как
написано выше; порядок импортов в файле определяет, какой модуль всплывёт
первым, суть ошибки та же).

- [x] **Step 4: Зависимости**

`execution/backend/app/api/deps.py`:

```python
"""Зависимости, общие для всех роутеров: сессия БД и текущий пользователь."""

from fastapi import Cookie, Depends, HTTPException
from jose import JWTError
from sqlalchemy.orm import Session

from app.api.security import decode_access_token
from app.config import config
from app.db import SessionLocal
from app.models.user import User


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if access_token is None:
        raise HTTPException(401, "не авторизован")
    try:
        payload = decode_access_token(access_token, config.jwt_secret)
    except JWTError:
        raise HTTPException(401, "недействительный токен")

    user = db.get(User, payload["user_id"])
    if user is None or not user.is_active:
        raise HTTPException(401, "пользователь не найден")
    return user


def require_role(*roles: str):
    """Фабрика зависимости: `Depends(require_role("admin"))`.

    Роль проверяется на бэкенде для каждого запроса — сокрытие пунктов меню
    на фронте защитой периметра не является.
    """
    def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "недостаточно прав")
        return user
    return _dependency
```

- [x] **Step 5: Роутер авторизации**

`execution/backend/app/api/auth.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    hash_password,
    verify_password,
)
from app.config import config
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Хеш-заглушка постоянной «формы» для сравнения, когда пользователя нет или он
# неактивен: bcrypt должен отрабатывать всегда, иначе время ответа выдаёт наличие
# аккаунта даже при одинаковом теле ответа.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class UserProfile(BaseModel):
    email: str
    full_name: str
    role: str


@router.post("/login", response_model=UserProfile)
def login(
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # Email нормализуется на входе (не в колонке БД — она осталась
    # регистрозависимой, миграция ради этого сейчас избыточна): без этого
    # "Admin@K1.ru" и "admin@k1.ru" — разные пользователи для БД, и админ,
    # набравший почту в регистре, который подставил его собственный почтовый
    # клиент, не может войти. Самовосстановления при этом нет — только shell
    # в контейнер. См. также create_admin.py и Task 19 (создание/правка
    # пользователя должны нормализовать email тем же способом).
    email = form.username.strip().lower()
    user = db.scalars(select(User).where(User.email == email)).first()
    password_hash = user.password_hash if user and user.is_active else _DUMMY_HASH
    password_ok = verify_password(form.password, password_hash)
    if user is None or not user.is_active or not password_ok:
        raise HTTPException(401, "неверный email или пароль")

    token = create_access_token(user.id, user.role, secret=config.jwt_secret)
    response.set_cookie(
        "access_token", token,
        # samesite="strict", а не "lax": lax — site-scoped, не origin-scoped,
        # и приложение на соседнем поддомене того же домена (а в разработке —
        # что угодно на другом порту localhost) всё ещё считается same-site и
        # получит cookie в cross-origin запросах. Панель — SPA без входящих
        # внешних ссылок, поэтому strict здесь бесплатен: первая межсайтовая
        # навигация cookie не получит, а все запросы SPA после загрузки идут
        # с её собственного origin и остаются same-site.
        httponly=True, samesite="strict", secure=config.cookie_secure,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return UserProfile(email=user.email, full_name=user.full_name, role=user.role)


@router.post("/logout")
def logout(response: Response):
    # Атрибуты зеркалят set_cookie в login(): идентичность cookie для браузера
    # — это имя+домен+путь, и delete_cookie сработал бы и без httponly/
    # samesite/secure, но асимметрия между установкой и удалением одной и той
    # же cookie — лишний повод для будущей путаницы при правке одного места
    # без другого.
    response.delete_cookie(
        "access_token",
        httponly=True, samesite="strict", secure=config.cookie_secure,
    )
    return {"ok": True}


@router.get("/me", response_model=UserProfile)
def me(user: User = Depends(get_current_user)):
    return UserProfile(email=user.email, full_name=user.full_name, role=user.role)
```

- [x] **Step 6: Сборка приложения**

`execution/backend/app/main.py`:

```python
"""Сборка FastAPI-приложения.

Два намеренных решения, зафиксированных здесь, чтобы их не «починили» при
будущей правке:

1. CORS отсутствует осознанно, не по недосмотру. И в разработке (Vite
   проксирует `/api` на бэкенд), и в проде (nginx, Task 26) фронт и API
   обслуживаются с одного origin — добавление `CORSMiddleware` было бы
   регрессом, открывающим API для чтения с чужих origin.
2. `/docs`, `/redoc`, `/openapi.json` включены по умолчанию FastAPI и не
   отключены. Сегодня это безопасно: nginx (Task 26) проксирует наружу
   только `location /api/`, а корневой `/` отдаёт статику SPA — сам FastAPI
   снаружи недостижим. Если этот блок nginx когда-нибудь расширят до
   `location /`, вся схема API станет публично перечислимой через `/docs`.
"""

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import auth
from app.api.deps import get_db

app = FastAPI(title="k1 content service")

app.include_router(auth.router)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    # Дымовая проверка после выкладки (DEPLOY.md, Task 26) полагается на этот
    # эндпоинт как на единственный сигнал "сервис работает". Статический
    # {"status": "ok"} отвечал бы так же и при лежащем Postgres — реальный
    # запрос к БД превращает проверку из "жив ли uvicorn" в "работает ли
    # сервис".
    db.execute(text("select 1"))
    return {"status": "ok"}
```

- [x] **Step 7: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_auth.py -v`
Expected: PASS — 6 passed (на момент этого шага; после ревью и добавления
тестов на `require_role` и деактивацию активной сессии — см. «Дополнение» в
Step 1 и Step 2 — в файле 10 тестов, все проходят)

- [x] **Step 8: Скрипт первого администратора**

`execution/backend/create_admin.py`:

```python
"""Разовое создание первого администратора: через UI его создать нельзя
(курица и яйцо — все админские эндпоинты требуют роли admin).

Запуск: docker compose run --rm backend python create_admin.py
"""

import getpass
import sys

from sqlalchemy import select

from app.api.security import hash_password
from app.db import SessionLocal
from app.models.user import User


def main() -> None:
    # .lower() зеркалит нормализацию в app/api/auth.py:login — без неё
    # admin, созданный с любой заглавной буквой в почте, не сможет войти:
    # колонка email регистрозависима, а почтовый клиент показывает и
    # подставляет адрес как ему вздумается.
    email = input("Email: ").strip().lower()
    full_name = input("Имя: ").strip()
    if not email:
        print("Email не может быть пустым")
        sys.exit(1)
    if not full_name:
        print("Имя не может быть пустым")
        sys.exit(1)
    password = getpass.getpass("Пароль: ")
    if getpass.getpass("Пароль ещё раз: ") != password:
        print("Пароли не совпадают")
        sys.exit(1)
    # Нижняя граница — в символах (len() строки), верхняя (BCRYPT_MAX_BYTES в
    # hash_password) — в байтах UTF-8. Сегодня это безвредно: даже 8 символов
    # из четырёхбайтовых code point'ов — это 32 байта, всё ещё далеко от 72.
    # Но единицы разные, и рядом стоящие проверки в разных единицах —
    # приглашение перепутать их при будущей правке нижней границы.
    if len(password) < 8:
        print("Пароль короче 8 символов")
        sys.exit(1)
    try:
        password_hash = hash_password(password)
    except ValueError as e:
        # hash_password бросает ValueError на пароле длиннее 72 байт (bcrypt
        # молча обрезал бы его иначе, см. app/api/security.py) — превращаем
        # в понятное сообщение, а не даём упасть трейсбеком.
        print(str(e))
        sys.exit(1)

    db = SessionLocal()
    try:
        if db.scalars(select(User).where(User.email == email)).first():
            print(f"Пользователь {email} уже существует")
            sys.exit(1)
        db.add(User(email=email, full_name=full_name,
                    password_hash=password_hash, role="admin", is_active=True))
        db.commit()
        print(f"Администратор {email} создан")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [x] **Step 9: Проверить скрипт вживую**

Run:
```bash
cd execution && docker compose up -d postgres && docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python create_admin.py
```
Expected: после ввода данных — `Администратор <email> создан`

- [x] **Step 10: Commit**

```bash
git add execution/backend/app execution/backend/create_admin.py execution/backend/tests
git commit -m "feat: авторизация, роли, первый администратор"
```

---

## Фаза 1 — Секреты, настройки, RouterAI

### Task 5: Шифрование секретов и SettingsService

**Files:**
- Create: `execution/backend/app/settings/__init__.py`
- Create: `execution/backend/app/settings/crypto.py`
- Create: `execution/backend/app/settings/service.py`
- Create: `execution/backend/app/models/setting.py`
- Modify: `execution/backend/app/models/__init__.py` (зарегистрировать `Setting`)
- Test: `execution/backend/tests/test_settings.py`

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_settings.py`:

```python
import pytest
from cryptography.fernet import Fernet

from app.settings.crypto import SecretDecryptionError, decrypt, encrypt, mask
from app.settings.service import SettingsService

KEY = Fernet.generate_key().decode()


def test_encrypt_roundtrip():
    assert decrypt(encrypt("token-123", KEY), KEY) == "token-123"


def test_encrypt_output_is_not_plaintext():
    assert "token-123" not in encrypt("token-123", KEY)


def test_decrypt_with_other_key_raises():
    other = Fernet.generate_key().decode()
    with pytest.raises(SecretDecryptionError):
        decrypt(encrypt("token-123", KEY), other)


def test_encrypt_rejects_malformed_key():
    """Обрезанный ключ или hex вместо base64 проходит проверку prod-compose
    (${ENCRYPTION_KEY:?...} ловит только пустую строку), но не Fernet —
    здесь это должно стать понятной ошибкой, а не голым ValueError."""
    with pytest.raises(SecretDecryptionError, match="невалиден"):
        encrypt("token-123", "not-a-valid-fernet-key")


def test_decrypt_rejects_malformed_key():
    ciphertext = encrypt("token-123", KEY)
    with pytest.raises(SecretDecryptionError, match="невалиден"):
        decrypt(ciphertext, "not-a-valid-fernet-key")


def test_decrypt_error_messages_distinguish_bad_key_from_wrong_key():
    """«Ключ невалиден по формату» и «значение зашифровано другим ключом» —
    разные причины и разные действия админа, сообщения не должны совпадать."""
    ciphertext = encrypt("token-123", KEY)
    other_key = Fernet.generate_key().decode()

    with pytest.raises(SecretDecryptionError, match="невалиден") as bad_format:
        decrypt(ciphertext, "not-a-valid-fernet-key")
    with pytest.raises(SecretDecryptionError, match="другим ключом") as wrong_key:
        decrypt(ciphertext, other_key)

    assert str(bad_format.value) != str(wrong_key.value)


def test_mask_hides_middle():
    assert mask("abcdefghijklmnop") == "abc...mnop"


def test_mask_hides_value_just_below_threshold():
    """Порог MIN_MASKABLE_LEN = 12. У 11-символьного значения формула «три
    первых плюс четыре последних» перекрывала бы 7 из 11 символов — почти
    ничего не скрыто, поэтому ниже порога значение прячется целиком."""
    assert mask("sk-abcdefgh") == "***"


def test_mask_shows_head_and_tail_at_threshold():
    """12 символов — ровно порог: голова и хвост уже не пересекаются."""
    assert mask("abcdefghijkl") == "abc...ijkl"


def test_mask_empty_value_is_fully_hidden():
    assert mask("") == "***"


def test_service_stores_plain_value(db_session):
    service = SettingsService(db_session, KEY)
    service.set("text_model", "anthropic/claude-sonnet-4-6")
    assert service.get_str("text_model") == "anthropic/claude-sonnet-4-6"


def test_service_stores_secret_encrypted(db_session):
    from app.models.setting import Setting

    service = SettingsService(db_session, KEY)
    service.set_secret("routerai_api_key", "sk-real-key")
    row = db_session.get(Setting, "routerai_api_key")
    assert row.is_secret is True
    assert "sk-real-key" not in row.value
    assert service.get_secret("routerai_api_key") == "sk-real-key"


def test_service_get_str_on_secret_key_raises(db_session):
    """is_secret не читается боевым кодом Task 6 (секретность там определяет
    отдельная константа SECRET_KEYS) — эта проверка защита от случайного
    get_str() по секретному ключу где-то ещё, который иначе тихо отдал бы
    шифротекст вида gAAAAABq... вызывающему."""
    service = SettingsService(db_session, KEY)
    service.set_secret("routerai_api_key", "sk-real-key")

    with pytest.raises(SecretDecryptionError):
        service.get_str("routerai_api_key")


def test_service_set_after_set_secret_clears_is_secret(db_session):
    """Переход секрет → обычное значение: set() обязан сбросить is_secret,
    иначе следующий get_str() на этот же ключ упадёт (см. проверку выше),
    хотя значение уже не секрет."""
    from app.models.setting import Setting

    service = SettingsService(db_session, KEY)
    service.set_secret("routerai_api_key", "sk-real-key")
    service.set("routerai_api_key", "not-a-secret-anymore")

    row = db_session.get(Setting, "routerai_api_key")
    assert row.is_secret is False
    assert service.get_str("routerai_api_key") == "not-a-secret-anymore"


def test_service_defaults(db_session):
    service = SettingsService(db_session, KEY)
    assert service.get_str("absent", "default") == "default"
    assert service.get_int("absent", 4) == 4
    assert service.get_bool("absent", True) is True


def test_service_get_secret_returns_default_when_never_set(db_session):
    """Путь, по которому Task 6 идёт при каждом GET настроек до того, как
    админ впервые введёт ключ: строки в settings ещё нет вовсе."""
    service = SettingsService(db_session, KEY)
    assert service.get_secret("routerai_api_key", "d") == "d"


def test_service_get_secret_with_other_key_raises(db_session):
    """Ветка в сервисе (не только в crypto.decrypt): при смене ENCRYPTION_KEY
    get_secret обязан упасть с понятным сообщением, а не молча вернуть
    default — иначе клиент RouterAI из Task 7 получит невнятный 401 вместо
    причины. Сообщение должно называть конкретную настройку — их два десятка."""
    service = SettingsService(db_session, KEY)
    service.set_secret("routerai_api_key", "sk-real-key")

    other_key = Fernet.generate_key().decode()
    other_service = SettingsService(db_session, other_key)

    with pytest.raises(SecretDecryptionError, match="routerai_api_key"):
        other_service.get_secret("routerai_api_key")


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("", False),
    ],
)
def test_service_get_bool_parses_stored_value(db_session, stored, expected):
    service = SettingsService(db_session, KEY)
    service.set("flag", stored)
    assert service.get_bool("flag", not expected) is expected


def test_service_get_int_parses_stored_value(db_session):
    service = SettingsService(db_session, KEY)
    service.set("max_articles_per_day", "42")
    assert service.get_int("max_articles_per_day", 0) == 42


def test_service_set_retries_once_on_concurrent_first_insert(db_session, monkeypatch):
    """Гонка на первой записи ключа: наша сессия не видит строку (SELECT —
    промах) и готовит INSERT, но кто-то другой успевает вставить и
    закоммитить строку с тем же key первым — Task 6 зовёт seed_settings на
    каждом GET, так что два админа на пустой БД сталкиваются здесь же.
    commit() должен словить конфликт первичного ключа и повторить операцию
    один раз как UPDATE, а не отдать вызывающему голый IntegrityError."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    real_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            # Симулируем конкурента: он вставляет и коммитит первым, реально
            # занимая строку в БД, прежде чем наш собственный commit() (уже
            # пытающийся вставить ту же строку) получит отказ по PK.
            db_session.rollback()
            db_session.execute(
                text(
                    "INSERT INTO settings (key, value, is_secret) "
                    "VALUES ('max_articles_per_day', '1', 0)"
                )
            )
            real_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)

    service = SettingsService(db_session, KEY)
    service.set("max_articles_per_day", "42")

    assert calls["n"] == 2
    assert service.get_str("max_articles_per_day") == "42"
```

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.settings'`

- [x] **Step 3: crypto.py**

`execution/backend/app/settings/__init__.py` — пустой файл.

`execution/backend/app/settings/crypto.py`:

```python
from cryptography.fernet import Fernet, InvalidToken

# Ниже этой длины показывать голову и хвост нельзя: у 8-символьного значения
# «три первых плюс четыре последних» скрывает ровно один символ.
MIN_MASKABLE_LEN = 12


class SecretDecryptionError(RuntimeError):
    """Значение не расшифровывается текущим ключом — обычно ENCRYPTION_KEY
    сменили, а настройки в БД остались зашифрованными старым."""


def _fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        # Пустой ключ в проде исключён (${ENCRYPTION_KEY:?...} в compose), но
        # обрезанный или не-base64 (например, вставили hex) — нет: он
        # проходит проверку compose и падает именно здесь, ровно на том
        # экране, куда админ идёт разбираться.
        raise SecretDecryptionError(
            "ENCRYPTION_KEY невалиден: ожидаются 32 url-safe base64-байта"
        ) from exc


def encrypt(value: str, key: str) -> str:
    return _fernet(key).encrypt(value.encode()).decode()


def decrypt(value: str, key: str) -> str:
    try:
        return _fernet(key).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError("значение зашифровано другим ключом") from exc


def mask(value: str) -> str:
    """Для отдачи в API: узнаваемо, но бесполезно для злоупотребления."""
    if len(value) < MIN_MASKABLE_LEN:
        return "***"
    return f"{value[:3]}...{value[-4:]}"
```

- [x] **Step 4: Модель Setting**

`execution/backend/app/models/setting.py`:

```python
from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
```

- [x] **Step 5: SettingsService**

`execution/backend/app/settings/service.py`:

```python
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.setting import Setting
from app.settings.crypto import SecretDecryptionError, decrypt, encrypt


class SettingsService:
    """Единственная точка доступа к бизнес-настройкам.

    Обычные значения хранятся как есть, секреты — зашифрованными.
    """

    def __init__(self, db: Session, encryption_key: str):
        self.db = db
        self.key = encryption_key

    def _row(self, name: str) -> Setting | None:
        return self.db.get(Setting, name)

    def _raw(self, name: str) -> str | None:
        row = self._row(name)
        return row.value if row else None

    def _upsert(self, name: str, value: str, is_secret: bool, commit: bool) -> None:
        row = self._row(name)
        if row is not None:
            row.value = value
            row.is_secret = is_secret
        else:
            self.db.add(Setting(key=name, value=value, is_secret=is_secret))
        if not commit:
            return
        try:
            self.db.commit()
        except IntegrityError:
            # Конкурентная первая запись: между нашим SELECT (промах) и
            # INSERT кто-то другой уже вставил строку с этим key — не только
            # фоновые воркеры, Task 6 зовёт seed_settings на каждом GET, так
            # что два админа, открывшие страницу настроек на пустой БД,
            # сталкиваются здесь же. К моменту повтора строка уже есть —
            # оставшийся путь превращает его в UPDATE. Повторяем один раз;
            # если и это не помогло — поднимаем настоящую причину.
            self.db.rollback()
            row = self._row(name)
            row.value = value
            row.is_secret = is_secret
            self.db.commit()

    def set(self, name: str, value: str, commit: bool = True) -> None:
        self._upsert(name, value, is_secret=False, commit=commit)

    def set_secret(self, name: str, value: str, commit: bool = True) -> None:
        self._upsert(name, encrypt(value, self.key), is_secret=True, commit=commit)

    def get_secret(self, name: str, default: str = "") -> str:
        raw = self._raw(name)
        if not raw:
            return default
        try:
            return decrypt(raw, self.key)
        except SecretDecryptionError:
            # Молча вернуть default нельзя: сервис пойдёт в RouterAI без ключа
            # и получит невнятный 401 вместо причины.
            raise SecretDecryptionError(
                f"настройка {name!r} зашифрована другим ключом — проверь "
                f"ENCRYPTION_KEY или перезапиши значение через админку"
            ) from None

    def get_str(self, name: str, default: str = "") -> str:
        row = self._row(name)
        if row is None:
            return default
        if row.is_secret:
            # is_secret не читается боевым кодом Task 6 (там своя константа
            # SECRET_KEYS), но эта проверка закрывает класс утечек, если
            # get_str когда-нибудь вызовут по секретному ключу по ошибке —
            # без неё вызывающий тихо получил бы шифротекст.
            raise SecretDecryptionError(
                f"настройка {name!r} хранится зашифрованной — используйте get_secret()"
            )
        return row.value

    def get_int(self, name: str, default: int) -> int:
        raw = self._raw(name)
        return int(raw) if raw is not None else default

    def get_bool(self, name: str, default: bool) -> bool:
        raw = self._raw(name)
        return raw.lower() in ("1", "true", "yes") if raw is not None else default
```

- [x] **Step 6: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_settings.py -v`
Expected: PASS — 25 passed (после ревью и правок; изначально — 8 passed)

- [x] **Step 7: Миграция**

Добавь `Setting` в `app/models/__init__.py` (реестр моделей, см. Task 2, Step 3) —
`alembic/env.py` и `tests/conftest.py` подхватят её через `import app.models` без
собственных правок. Затем:

Run:
```bash
cd execution && docker compose run --rm backend alembic revision --autogenerate -m "settings"
docker compose run --rm backend alembic upgrade head
```
Expected: `Running upgrade <prev> -> <hash>, settings`

- [x] **Step 8: Commit**

```bash
git add execution/backend/app/settings execution/backend/app/models/setting.py execution/backend/alembic execution/backend/tests/test_settings.py
git commit -m "feat: шифрование секретов и SettingsService"
```

---

### Task 6: Дефолтные настройки и API настроек

**Files:**
- Create: `execution/backend/app/seed.py`
- Create: `execution/backend/app/api/admin_settings.py`
- Modify: `execution/backend/app/main.py`
- Test: `execution/backend/tests/test_api_admin_settings.py`

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_api_admin_settings.py`:

```python
def test_manager_cannot_read_settings(manager_client):
    assert manager_client.get("/api/admin/settings").status_code == 403


def test_admin_reads_defaults(admin_client):
    resp = admin_client.get("/api/admin/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["image_model"] == "openai/gpt-image-2"
    assert body["image_quality"] == "medium"
    assert body["routerai_base_url"] == "https://routerai.ru/api/v1"


def test_secret_is_never_returned_raw(admin_client):
    admin_client.put("/api/admin/settings", json={"routerai_api_key": "sk-super-secret-value"})
    body = admin_client.get("/api/admin/settings").json()
    assert body["routerai_api_key"] == "sk-...alue"


def test_empty_secret_means_keep_current(admin_client, db_session):
    from app.config import config
    from app.settings.service import SettingsService

    admin_client.put("/api/admin/settings", json={"routerai_api_key": "sk-super-secret-value"})
    admin_client.put("/api/admin/settings", json={"routerai_api_key": "", "image_quality": "high"})

    service = SettingsService(db_session, config.encryption_key)
    assert service.get_secret("routerai_api_key") == "sk-super-secret-value"
    assert service.get_str("image_quality") == "high"


def test_update_plain_value(admin_client):
    admin_client.put("/api/admin/settings", json={"image_workers": "6"})
    assert admin_client.get("/api/admin/settings").json()["image_workers"] == "6"


def test_invalid_int_setting_rejected(admin_client):
    """image_workers валидируется на PUT, до записи — иначе опечатка проходит
    с 200 и падает позже необработанным ValueError внутри celery-таски
    (Task 8), где её уже никто не увидит."""
    resp = admin_client.put("/api/admin/settings", json={"image_workers": ""})
    assert resp.status_code == 422


def test_image_workers_accepts_boundary_values(admin_client):
    for value in ("1", "8"):
        resp = admin_client.put("/api/admin/settings", json={"image_workers": value})
        assert resp.status_code == 200
        assert resp.json()["image_workers"] == value


def test_image_workers_rejects_out_of_range(admin_client):
    """image_workers ограничен не только «целое число»: 0 и отрицательные
    валят ThreadPoolExecutor(max_workers=...) необработанным ValueError
    внутри celery-таски (Task 8), а без верхней границы опечатка вроде «40»
    вместо «4» линейно растит число потоков и память на каждую партию
    статей. Сообщение об ошибке обязано называть допустимый диапазон."""
    for value in ("0", "-1", "9", "40"):
        resp = admin_client.put("/api/admin/settings", json={"image_workers": value})
        assert resp.status_code == 422
        assert "1" in resp.json()["detail"] and "8" in resp.json()["detail"]


def test_secret_decryption_error_returns_empty_value_and_errors_key(admin_client, db_session):
    """Если ENCRYPTION_KEY сменили после того, как секрет сохранён старым
    ключом, get_secret бросает SecretDecryptionError. Поле секрета обязано
    остаться пустым (безопасно для round-trip в PUT — пустая строка значит
    «не менять»), а диагностика уходит в отдельный ключ _errors, откуда её
    точно не отправят обратно как значение."""
    from cryptography.fernet import Fernet

    from app.config import config

    admin_client.put("/api/admin/settings", json={"routerai_api_key": "sk-super-secret-value"})

    original_key = config.encryption_key
    config.encryption_key = Fernet.generate_key().decode()
    try:
        body = admin_client.get("/api/admin/settings").json()
    finally:
        config.encryption_key = original_key

    assert body["routerai_api_key"] == ""
    assert "routerai_api_key" in body["_errors"]


def test_get_response_echoed_back_does_not_overwrite_secret(admin_client, db_session):
    """Регрессия на найденный сценарий потери данных: раньше GET при чужом
    ENCRYPTION_KEY клал в поле секрета текст ошибки («ОШИБКА: ...»), и если
    фронт отправлял ответ GET целиком обратно в PUT (обычный паттерн «сохранить
    всю форму»), этот текст шифровался поверх настоящего ключа — и уже
    ничем не восстанавливался, даже возвратом правильного ENCRYPTION_KEY."""
    from cryptography.fernet import Fernet

    from app.config import config
    from app.settings.service import SettingsService

    admin_client.put("/api/admin/settings", json={"routerai_api_key": "sk-super-secret-value"})

    original_key = config.encryption_key
    config.encryption_key = Fernet.generate_key().decode()
    try:
        body = admin_client.get("/api/admin/settings").json()
        admin_client.put("/api/admin/settings", json=body)
    finally:
        config.encryption_key = original_key

    service = SettingsService(db_session, config.encryption_key)
    assert service.get_secret("routerai_api_key") == "sk-super-secret-value"


def test_seed_settings_retries_once_on_concurrent_insert(db_session, monkeypatch):
    """seed_settings пишет через db.add напрямую, минуя защищённый от гонки
    SettingsService._upsert, и вызывается на каждом GET — тот же класс
    гонки, что чинили в Task 5, но здесь без защиты. Симулируем конкурента,
    вставившего и закоммитившего одну из дефолтных настроек первым (между
    нашим SELECT-проходом и commit()): наш commit() обязан поймать конфликт
    первичного ключа, откатиться и повторить один раз, а не отдать наружу
    голый IntegrityError (→ 500 у одного из двух админов, открывших
    страницу настроек одновременно на пустой БД)."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.models.setting import Setting
    from app.seed import DEFAULT_SETTINGS, seed_settings

    real_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            # Конкурент реально занимает строку в БД первым, прежде чем наш
            # собственный commit() (уже пытающийся вставить ту же строку)
            # получит отказ по PK.
            db_session.rollback()
            db_session.execute(
                text(
                    "INSERT INTO settings (key, value, is_secret) "
                    "VALUES ('text_model', 'concurrent-value', 0)"
                )
            )
            real_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)

    seed_settings(db_session)

    assert calls["n"] == 2
    # Конкурентно вставленное значение не перезаписывается — идемпотентность
    # seed_settings сохраняется и на повторе после гонки.
    assert db_session.get(Setting, "text_model").value == "concurrent-value"
    for key, value in DEFAULT_SETTINGS.items():
        if key == "text_model":
            continue
        assert db_session.get(Setting, key).value == value


def test_put_does_not_call_seed_settings_again(admin_client, monkeypatch):
    """PUT собирает ответ через отдельную от GET функцию и не должен зависеть
    от повторного наполнения дефолтов (со своим отдельным commit() внутри
    seed_settings) — иначе сбой в этом наполнении превращает уже сохранённые
    изменения в ложный 500 у клиента, хотя сам payload уже записан."""
    import app.api.admin_settings as admin_settings_module

    def boom(db):
        raise RuntimeError("seed_settings не должен вызываться из PUT")

    monkeypatch.setattr(admin_settings_module, "seed_settings", boom)

    resp = admin_client.put("/api/admin/settings", json={"image_workers": "6"})
    assert resp.status_code == 200
    assert resp.json()["image_workers"] == "6"
```

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_settings.py -v`
Expected: FAIL — 404 на `/api/admin/settings`

- [x] **Step 3: Дефолты**

`execution/backend/app/seed.py`:

```python
"""Дефолтные значения настроек и промптов. Идемпотентна: существующие
записи не перезаписываются — отредактированный в админке промпт переживает
перезапуск сервиса."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.setting import Setting

# Ключ RouterAI сюда не входит: у него нет осмысленного дефолта, он вводится
# админом и хранится зашифрованным.
DEFAULT_SETTINGS = {
    "routerai_base_url": "https://routerai.ru/api/v1",
    "text_model": "anthropic/claude-sonnet-4-6",
    "image_model": "openai/gpt-image-2",
    "image_quality": "medium",   # high дороже втрое: ≈16.8 против ≈5.4 за кадр
    "image_size": "1536x1024",
    "image_workers": "4",
    "llm_max_retries": "3",
}

SECRET_KEYS = {"routerai_api_key"}

# int-настройки валидируются на PUT до записи (см. admin_settings.py) —
# иначе опечатка проходит с 200 и падает позже необработанным ValueError
# внутри celery-таски (Task 8), где её уже никто не увидит.
INT_KEYS = {"image_workers", "llm_max_retries"}

# Диапазоны для тех int-настроек, которым мало быть просто целым числом.
# image_workers — ширина ThreadPoolExecutor(max_workers=...) в Task 8/16:
# 0 и отрицательные валят Celery-таску необработанным ValueError (max_workers
# must be greater than 0), а сверху границы не было вовсе — опечатка вроде
# «40» вместо «4» линейно растит число потоков и память на каждую партию
# статей. 8 — с запасом выше практического максимума, 1 — минимум, при
# котором параллелизм просто вырождается в последовательную генерацию.
# Данные, а не условие в роутере: следующие настройки с границами
# добавляются сюда же, без правки app/api/admin_settings.py.
INT_RANGES = {"image_workers": (1, 8)}


def seed_settings(db: Session) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value, is_secret=False))
    try:
        db.commit()
    except IntegrityError:
        # Конкурентный seed_settings: вызывается на каждом GET
        # /api/admin/settings, так что два админа, открывшие страницу
        # настроек на пустой БД одновременно, оба проходят SELECT-фазу
        # (видят пусто) раньше, чем кто-то из них коммитит — тот же класс
        # гонки, что чинили в Task 5 для SettingsService._upsert. Один из
        # них коммитит первым и захватывает часть или все дефолтные ключи;
        # наш commit() ловит конфликт первичного ключа. Откатываем и
        # смотрим заново: то, что конкурент уже вставил, теперь видно и не
        # добавляется повторно (идемпотентность), то, что всё ещё
        # отсутствует — довставляем и коммитим один раз.
        db.rollback()
        for key, value in DEFAULT_SETTINGS.items():
            if db.get(Setting, key) is None:
                db.add(Setting(key=key, value=value, is_secret=False))
        db.commit()
```

- [x] **Step 4: Роутер настроек**

`execution/backend/app/api/admin_settings.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.config import config
from app.models.user import User
from app.seed import DEFAULT_SETTINGS, INT_KEYS, INT_RANGES, SECRET_KEYS, seed_settings
from app.settings.crypto import SecretDecryptionError, mask
from app.settings.service import SettingsService

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


def _service(db: Session) -> SettingsService:
    return SettingsService(db, config.encryption_key)


def _current_settings(db: Session) -> dict:
    """Собирает текущие настройки в ответ. Не вызывает seed_settings — тот
    вызывается ровно один раз за GET из read_settings, а не отсюда: PUT
    зовёт эту функцию после своего единственного коммита, и повторное
    наполнение дефолтов со своим отдельным commit() внутри было бы лишней
    точкой отказа поверх уже сохранённых изменений (если бы оно упало,
    клиент получил бы 500, хотя запрошенные им изменения уже записаны)."""
    service = _service(db)
    result = {key: service.get_str(key, default) for key, default in DEFAULT_SETTINGS.items()}
    errors: dict[str, str] = {}
    for key in SECRET_KEYS:
        try:
            value = service.get_secret(key)
        except SecretDecryptionError as exc:
            # Пустая строка, а не текст ошибки: это же поле уходит через PUT
            # обратно как «новое значение», если фронт когда-нибудь пришлёт
            # форму целиком, — а любая непустая строка в этом поле
            # шифруется и сохраняется как настоящий секрет (см. PUT ниже).
            # Раньше сюда клали f"ОШИБКА: {exc}" — при round-trip GET → PUT
            # это необратимо затирало настоящий ключ текстом диагностики.
            # Пустая строка уже означает «не менять» по контракту PUT, так
            # что round-trip перестаёт быть разрушительным по построению.
            # Сама диагностика уходит в отдельный ключ _errors, откуда её
            # точно не отправят обратно как значение.
            result[key] = ""
            errors[key] = str(exc)
            continue
        result[key] = mask(value) if value else ""
    if errors:
        result["_errors"] = errors
    return result


@router.get("")
def read_settings(db: Session = Depends(get_db),
                  _user: User = Depends(require_role("admin"))) -> dict:
    seed_settings(db)
    return _current_settings(db)


@router.put("")
def update_settings(payload: dict, db: Session = Depends(get_db),
                    _user: User = Depends(require_role("admin"))) -> dict:
    # int-настройки валидируются здесь, до записи — иначе опечатка проходит
    # с 200 и падает позже необработанным ValueError внутри celery-таски
    # (Task 8), где её уже никто не увидит. Часть из них дополнительно
    # ограничена диапазоном (INT_RANGES) — «целое число» само по себе не
    # спасает от 0 (ThreadPoolExecutor(max_workers=0) тоже падает необработанным
    # ValueError) или от опечатки вроде «40» вместо «4».
    errors = []
    for key, value in payload.items():
        if key in INT_KEYS:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                errors.append(f"настройка {key!r} должна быть целым числом")
                continue
            bounds = INT_RANGES.get(key)
            if bounds and not (bounds[0] <= parsed <= bounds[1]):
                errors.append(
                    f"настройка {key!r} должна быть целым числом "
                    f"от {bounds[0]} до {bounds[1]}")
    if errors:
        raise HTTPException(422, "; ".join(errors))

    service = _service(db)
    for key, value in payload.items():
        if key in SECRET_KEYS:
            # Пустая строка = «не менять»: фронт получает маску (или пустую
            # строку при ошибке расшифровки — см. _current_settings), а не
            # значение, и не может отправить секрет обратно неизменным.
            if value:
                service.set_secret(key, str(value), commit=False)
        elif key in DEFAULT_SETTINGS:
            service.set(key, str(value), commit=False)
    # Один коммит на весь payload: несколько ключей в одном PUT либо
    # применяются все разом, либо ни один — иначе ошибка на середине списка
    # оставляет половину настроек изменённой, а половину нет.
    db.commit()
    # _current_settings, а не read_settings(db, _user): без повторного
    # seed_settings и без вызова функции-эндпоинта из другого эндпоинта —
    # сборка ответа отделена от наполнения дефолтов.
    return _current_settings(db)
```

- [x] **Step 5: Подключить роутер**

В `execution/backend/app/main.py` заменить блок импорта и подключения на:

```python
from app.api import admin_settings, auth

app.include_router(auth.router)
app.include_router(admin_settings.router)
```

- [x] **Step 6: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_settings.py -v`
Expected: PASS — 12 passed

- [x] **Step 7: Commit**

```bash
git add execution/backend/app/seed.py execution/backend/app/api/admin_settings.py execution/backend/app/main.py execution/backend/tests/test_api_admin_settings.py
git commit -m "feat: настройки RouterAI с маскированием секретов"
```

- [x] **Step 8: Замечания ревью (при Task 8) — границы image_workers**

Ревью Task 8 показало эмпирически: `ThreadPoolExecutor(max_workers=0)` и
с отрицательным числом валит Celery-таску необработанным `ValueError`, а
верхней границы на `image_workers` не было вовсе — опечатка вроде «40»
вместо «4» линейно растит число потоков и память на каждую партию статей
(при `image_workers=16` рост RSS составил ≈420 МБ на одну картиночную фазу,
при двух воркерах Celery в одном контейнере — вдвое больше). Добавлен
`INT_RANGES = {"image_workers": (1, 8)}` в `app/seed.py` (диапазон — данные,
а не условие в роутере) и проверка диапазона в PUT `admin_settings.py` с
сообщением, называющим границы. Код и тесты выше уже отражают исправление;
коммит — общий с Task 8 (см. его Step 8).

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_settings.py -v`
Expected: PASS — 12 passed

---

### Task 7: Текстовый клиент RouterAI

**Files:**
- Create: `execution/backend/app/ai/__init__.py`
- Create: `execution/backend/app/ai/text.py`
- Test: `execution/backend/tests/test_ai_text.py`

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_ai_text.py`:

```python
import logging
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.ai.text import REQUEST_TIMEOUT_SECONDS, LLMError, TextClient, build_client


class FakeCompletions:
    def __init__(self, content, usage_cost=0.5, fail_times=0):
        self.content = content
        self.usage_cost = usage_cost
        self.fail_times = fail_times
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("transport down")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, cost=self.usage_cost),
        )


def fake_client(content, **kwargs):
    completions = FakeCompletions(content, **kwargs)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def _api_error(cls, status_code: int, message: str):
    """Настоящий экземпляр исключения openai.* — не заглушка, чтобы проверить
    именно ветвление по классам, которое видит боевой код."""
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


class ExceptionCompletions:
    """Бросает заданное исключение первые `fail_times` вызовов, затем отвечает успешно."""

    def __init__(self, make_exc, fail_times=1, content="ок"):
        self.make_exc = make_exc
        self.fail_times = fail_times
        self.content = content
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.make_exc()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, cost=0.1),
        )


def test_complete_text_returns_content_and_usage():
    client, _ = fake_client("<p>Текст статьи</p>")
    result = TextClient(client, "test-model").complete_text("промпт")
    assert result.text == "<p>Текст статьи</p>"
    assert result.tokens_prompt == 10
    assert result.tokens_completion == 20
    assert result.cost == 0.5


def test_complete_json_strips_code_fence():
    client, _ = fake_client('```json\n["Тема 1", "Тема 2"]\n```')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["Тема 1", "Тема 2"]


def test_complete_json_rejects_non_json():
    client, _ = fake_client("извините, не могу")
    with pytest.raises(LLMError, match="не JSON"):
        TextClient(client, "test-model").complete_json("промпт")


def test_transport_failure_is_retried():
    client, completions = fake_client("ок", fail_times=2)
    result = TextClient(client, "test-model", max_retries=3).complete_text("промпт")
    assert result.text == "ок"
    assert completions.calls == 3


def test_gives_up_after_max_retries():
    client, _ = fake_client("ок", fail_times=5)
    with pytest.raises(LLMError, match="недоступна"):
        TextClient(client, "test-model", max_retries=3).complete_text("промпт")


def test_empty_content_is_an_error():
    client, _ = fake_client(None)
    with pytest.raises(LLMError, match="пустой content"):
        TextClient(client, "test-model").complete_text("промпт")


# --- избирательный ретрай: детерминированные отказы vs временные ---

def test_auth_error_is_not_retried():
    completions = ExceptionCompletions(
        lambda: _api_error(openai.AuthenticationError, 401, "invalid api key"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    with pytest.raises(LLMError, match="ключ"):
        TextClient(client, "test-model", max_retries=3).complete_text("промпт")
    assert completions.calls == 1


def test_bad_request_is_not_retried():
    completions = ExceptionCompletions(
        lambda: _api_error(openai.BadRequestError, 400, "malformed request"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    with pytest.raises(LLMError, match="некорректный"):
        TextClient(client, "test-model", max_retries=3).complete_text("промпт")
    assert completions.calls == 1


def test_rate_limit_is_retried():
    completions = ExceptionCompletions(
        lambda: _api_error(openai.RateLimitError, 429, "too many requests"), fail_times=2)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result = TextClient(client, "test-model", max_retries=3).complete_text("промпт")
    assert result.text == "ок"
    assert completions.calls == 3


def test_build_client_sets_timeout_and_disables_sdk_retries():
    client = build_client("https://routerai.ru/api/v1", "sk-test")
    assert client.timeout == REQUEST_TIMEOUT_SECONDS
    assert client.max_retries == 0


# --- разбор JSON с текстом вокруг огороженного блока ---

def test_complete_json_handles_text_before_and_after_fence():
    client, _ = fake_client('Вот темы:\n```json\n["Тема 1", "Тема 2"]\n```\nСпасибо!')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["Тема 1", "Тема 2"]


def test_complete_json_takes_first_of_several_fences():
    client, _ = fake_client('```json\n["A"]\n```\n```json\n["B"]\n```')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["A"]


def test_complete_json_handles_fence_without_language_tag():
    client, _ = fake_client('```\n["Тема"]\n```')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["Тема"]


def test_complete_json_handles_plain_json_without_fence():
    client, _ = fake_client('["Тема 1", "Тема 2"]')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["Тема 1", "Тема 2"]


def test_complete_json_keeps_triple_backticks_inside_string_intact():
    client, _ = fake_client('```json\n{"note": "see ```python``` block"}\n```')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == {"note": "see ```python``` block"}


# --- расход, о котором провайдер не сообщил ---

def test_missing_usage_cost_logs_warning(caplog):
    client, _ = fake_client("ок")

    def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ок"))],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7),  # без cost
        )

    client.chat.completions.create = create
    with caplog.at_level(logging.WARNING):
        result = TextClient(client, "test-model").complete_text("промпт")
    assert result.cost == 0.0
    assert "usage.cost" in caplog.text
    assert "test-model" in caplog.text
```

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai'`

- [x] **Step 3: Реализация**

`execution/backend/app/ai/__init__.py` — пустой файл.

`execution/backend/app/ai/text.py`:

```python
"""Текстовая часть RouterAI. Провайдер OpenAI-совместимый, поэтому смена
провайдера — это смена base_url и модели в настройках, без правки кода.
"""

import json
import logging
import re
import time
from dataclasses import dataclass

import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

# Огороженный блок markdown: ```json ... ``` или ``` ... ```. Открывающая и
# закрывающая метки должны занимать свою строку целиком — иначе тройные
# кавычки, случайно оказавшиеся внутри значения (например, в примере кода
# внутри текста статьи), обрывали бы блок раньше времени.
_FENCE = re.compile(
    r"^```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n^```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# Один вызов генерации текста статьи укладывается в это время с большим
# запасом; дефолт SDK (600 с на чтение) — это по сути «жди сколько хочешь»,
# а нам нужно, чтобы зависшее соединение не съедало слот Celery-воркера
# часами (см. app/celery_app.py — там свой предел на всю задачу).
REQUEST_TIMEOUT_SECONDS = 120.0

# Отказы, которые не изменятся при повторе: неверный ключ, нет доступа,
# некорректный запрос, модель/ресурс не найдены. Ретраить их — тратить время
# и попытки на заведомо тот же результат.
_NON_RETRYABLE = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.BadRequestError,
    openai.NotFoundError,
)


class LLMError(RuntimeError):
    pass


@dataclass
class TextResult:
    text: str
    tokens_prompt: int
    tokens_completion: int
    cost: float


@dataclass
class JsonResult:
    data: object
    tokens_prompt: int
    tokens_completion: int
    cost: float


def build_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        # Ретраями управляет TextClient. Если оставить дефолт SDK (2 внутренних
        # повтора), на каждую нашу попытку добавляются ещё до трёх HTTP-запросов
        # — при 429 это удваивает и без того лишнюю нагрузку на провайдера.
        max_retries=0,
    )


def _non_retryable_message(exc: Exception) -> str:
    if isinstance(exc, openai.AuthenticationError):
        return f"RouterAI отклонил ключ API — проверьте настройку routerai_api_key (401): {exc}"
    if isinstance(exc, openai.PermissionDeniedError):
        return f"RouterAI запретил доступ этим ключом (403): {exc}"
    if isinstance(exc, openai.BadRequestError):
        return f"RouterAI отклонил запрос как некорректный (400): {exc}"
    if isinstance(exc, openai.NotFoundError):
        return f"RouterAI не нашёл модель или ресурс (404): {exc}"
    return f"RouterAI отказал в запросе без права на повтор: {exc}"


class TextClient:
    def __init__(self, client, model: str, max_retries: int = 3, backoff: float = 0.0,
                 temperature: float = 0.7):
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.backoff = backoff
        self.temperature = temperature

    def complete_text(self, prompt: str) -> TextResult:
        response = self._call(prompt)
        return TextResult(self._content(response), *self._usage(response))

    def complete_json(self, prompt: str) -> JsonResult:
        response = self._call(prompt)
        content = self._content(response)
        match = _FENCE.search(content)
        raw = match.group("body").strip() if match else content.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"модель вернула не JSON: {raw[:200]}") from exc
        return JsonResult(data, *self._usage(response))

    def _call(self, prompt: str):
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                )
            except _NON_RETRYABLE as exc:
                raise LLMError(_non_retryable_message(exc)) from exc
            except Exception as exc:
                # Сбои транспорта и превышение лимита частоты (429) — тут
                # повтор осмыслен, в отличие от _NON_RETRYABLE выше.
                last_error = exc
                if self.backoff and attempt < self.max_retries - 1:
                    time.sleep(self.backoff * (2**attempt))
        raise LLMError(f"LLM недоступна после {self.max_retries} попыток: {last_error}")

    @staticmethod
    def _content(response) -> str:
        if not response.choices:
            raise LLMError("провайдер вернул ответ без вариантов")
        content = response.choices[0].message.content
        if content is None:
            raise LLMError("модель отказалась отвечать: пустой content")
        return content

    def _usage(self, response) -> tuple[int, int, float]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0, 0.0
        _missing = object()
        cost = getattr(usage, "cost", _missing)
        if cost is _missing:
            # usage.cost — расширение RouterAI, а не часть OpenAI API. Молчаливый
            # ноль неотличим от настоящего нуля, поэтому хотя бы в лог.
            logger.warning(
                "RouterAI не сообщил usage.cost для модели %s — стоимость записана как 0",
                self.model,
            )
            cost = 0.0
        return (
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            float(cost or 0.0),
        )
```

- [x] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_text.py -v`
Expected: PASS — 16 passed

- [x] **Step 5: Commit**

```bash
git add execution/backend/app/ai execution/backend/tests/test_ai_text.py
git commit -m "feat: текстовый клиент RouterAI"
```

---

### Task 8: Генерация картинок, кроп и водяной знак

Порт `execution/articles/gen_images.py` с заменой `.env`/манифеста на параметры вызова.
Водяной знак — пункт 2 из «Осталось доделать» в `orchestration/articles-plan.md`.

**Files:**
- Create: `execution/backend/app/ai/images.py`
- Create: `execution/backend/app/ai/watermark.py`
- Test: `execution/backend/tests/test_ai_images.py`
- Test: `execution/backend/tests/test_ai_watermark.py`

- [x] **Step 1: Написать падающий тест на кроп и упаковку**

`execution/backend/tests/test_ai_images.py`:

```python
import base64
import io

import pytest
from PIL import Image

from app.ai.images import ImageError, ImageGenerator, crop_to_ratio, to_webp


def png_bytes(width: int, height: int, color=(120, 140, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def test_crop_wide_image_to_square():
    cropped = crop_to_ratio(Image.new("RGB", (1536, 1024)), "1:1")
    assert cropped.size == (1024, 1024)


def test_crop_tall_image_to_wide():
    cropped = crop_to_ratio(Image.new("RGB", (1024, 1536)), "21:9")
    assert cropped.size == (1024, 438)


def test_crop_keeps_already_correct_ratio():
    cropped = crop_to_ratio(Image.new("RGB", (1536, 1024)), "3:2")
    assert cropped.size == (1536, 1024)


def test_to_webp_downscales_to_max_width():
    data, size = to_webp(png_bytes(2400, 1600), crop=None)
    assert size == (1600, 1066)
    assert Image.open(io.BytesIO(data)).format == "WEBP"


def test_to_webp_applies_crop_before_resize():
    _, size = to_webp(png_bytes(1536, 1024), crop="1:1")
    assert size == (1024, 1024)


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_generate_returns_webp_and_cost(monkeypatch):
    payload = {
        "data": [{"b64_json": base64.b64encode(png_bytes(1536, 1024)).decode()}],
        "usage": {"cost": 5.4},
    }
    monkeypatch.setattr("app.ai.images.requests.post",
                        lambda *a, **kw: FakeResponse(200, payload))
    result = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2").generate(
        prompt="дом в лесу", size="1536x1024", quality="medium", crop="3:2")
    assert result.cost == 5.4
    assert Image.open(io.BytesIO(result.data)).format == "WEBP"


def test_generate_retries_then_fails(monkeypatch):
    calls = []

    def always_500(*args, **kwargs):
        calls.append(1)
        return FakeResponse(500, text="upstream error")

    monkeypatch.setattr("app.ai.images.requests.post", always_500)
    monkeypatch.setattr("app.ai.images.time.sleep", lambda _s: None)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="500"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 3


# «200 OK, но тело не разобрать» — не лечится повтором (тот же провайдер с
# высокой вероятностью вернёт тот же мусор), поэтому каждый из следующих
# случаев обязан обернуться в ImageError и НЕ дёргать requests.post повторно,
# даже если max_retries > 1.

def _post_once_returning(monkeypatch, payload):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeResponse(200, payload)

    monkeypatch.setattr("app.ai.images.requests.post", fake_post)
    monkeypatch.setattr("app.ai.images.time.sleep", lambda _s: None)
    return calls


def test_generate_wraps_missing_data_key_as_image_error(monkeypatch):
    calls = _post_once_returning(monkeypatch, {"usage": {"cost": 1.0}})
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="разобрать"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1


def test_generate_wraps_non_image_payload_as_image_error(monkeypatch):
    garbage_b64 = base64.b64encode(b"this is not an image at all").decode()
    calls = _post_once_returning(
        monkeypatch, {"data": [{"b64_json": garbage_b64}], "usage": {"cost": 1.0}})
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="разобрать"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1


def test_generate_wraps_invalid_base64_as_image_error(monkeypatch):
    calls = _post_once_returning(
        monkeypatch, {"data": [{"b64_json": "!!!not-base64!!!"}], "usage": {"cost": 1.0}})
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="разобрать"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1


def test_generate_wraps_truncated_image_as_image_error(monkeypatch):
    full_png = png_bytes(800, 600)
    truncated_b64 = base64.b64encode(full_png[: len(full_png) // 2]).decode()
    calls = _post_once_returning(
        monkeypatch, {"data": [{"b64_json": truncated_b64}], "usage": {"cost": 1.0}})
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="разобрать"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1
```

- [x] **Step 2: Написать падающий тест на водяной знак**

`execution/backend/tests/test_ai_watermark.py`:

```python
import io

from PIL import Image, ImageChops

from app.ai.watermark import apply_watermark


def image_bytes(width, height, color, mode="RGB", fmt="PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), color).save(buffer, fmt)
    return buffer.getvalue()


def test_watermark_preserves_size():
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 200), mode="RGBA")
    result = apply_watermark(base, mark)
    assert Image.open(io.BytesIO(result)).size == (1600, 900)


def changed_box(base: bytes, marked: bytes):
    """Прямоугольник изменённых пикселей — где именно лёг знак."""
    before = Image.open(io.BytesIO(base)).convert("RGB")
    after = Image.open(io.BytesIO(marked)).convert("RGB")
    return ImageChops.difference(before, after).getbbox()


def test_watermark_lands_in_bottom_right_quadrant():
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 255), mode="RGBA")
    left, top, right, bottom = changed_box(base, apply_watermark(base, mark))
    assert left > 1600 / 2 and top > 900 / 2
    assert right <= 1600 and bottom <= 900


def test_watermark_is_small():
    """«Небольшой» — требование владельца, а не вкусовщина: знак помечает
    авторство, а не соперничает с содержимым кадра. Без этой проверки долю
    ширины можно молча увеличить, и никто не заметит."""
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 255), mode="RGBA")
    left, _, right, _ = changed_box(base, apply_watermark(base, mark))
    assert (right - left) <= 1600 * 0.15


def test_watermark_does_not_touch_edges():
    """Отступы от правого и нижнего краёв — тоже требование владельца."""
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 255), mode="RGBA")
    _, _, right, bottom = changed_box(base, apply_watermark(base, mark))
    assert right < 1600 - 1600 * 0.02
    assert bottom < 900 - 1600 * 0.02


def test_watermark_leaves_top_left_untouched():
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 255), mode="RGBA")
    after = Image.open(io.BytesIO(apply_watermark(base, mark))).convert("RGB")
    assert after.getpixel((20, 20)) == (10, 10, 10)


def test_oversized_watermark_is_scaled_down():
    """Знак шире картинки не должен обрезаться по краю — он масштабируется
    до доли ширины кадра."""
    base = image_bytes(800, 600, (10, 10, 10))
    mark = image_bytes(4000, 1000, (255, 255, 255, 255), mode="RGBA")
    result = apply_watermark(base, mark)
    assert Image.open(io.BytesIO(result)).size == (800, 600)


def test_empty_watermark_returns_original():
    base = image_bytes(800, 600, (10, 10, 10))
    assert apply_watermark(base, b"") == base
```

- [x] **Step 3: Запустить тесты, убедиться что падают**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_images.py tests/test_ai_watermark.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai.images'`

- [x] **Step 4: Реализация images.py**

`execution/backend/app/ai/images.py`:

```python
"""Генерация иллюстраций через RouterAI.

Модель gpt-image-2 отдаёт только фиксированные размеры (1024x1024, 1536x1024,
1024x1536) и игнорирует aspect_ratio — нужные пропорции получаем центральным
кропом. Порт execution/articles/gen_images.py: манифест и .env заменены
параметрами вызова, параллелизм поднят на уровень Celery-задачи.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass

import requests
from PIL import Image

MAX_WIDTH = 1600
WEBP_QUALITY = 82
# Один кадр в норме укладывается в 40-140 с (см. execution/articles/
# gen_images.py и практику), 180 с — щедрый запас под одну попытку. Худший
# случай всей generate() с max_retries=2: 180×2 + backoff(5×1) между ними =
# 365 с — та же величина, что и у худшего случая текстового вызова
# (app/ai/text.py: REQUEST_TIMEOUT_SECONDS=120 × 3 попытки + паузы ≈366 с).
# Раньше TIMEOUT=420 и max_retries=3 давали до ≈1275 с на одну картинку —
# втрое больше всего бюджета статьи (ARTICLE_TIME_BUDGET_SECONDS в Task 18).
TIMEOUT = 180


class ImageError(RuntimeError):
    pass


@dataclass
class ImageResult:
    data: bytes
    size: tuple[int, int]
    cost: float
    seconds: int


def crop_to_ratio(image: Image.Image, ratio: str) -> Image.Image:
    """Центральный кроп до заданного соотношения сторон ('21:9')."""
    rw, rh = (int(x) for x in ratio.split(":"))
    target = rw / rh
    width, height = image.size
    if width / height > target:
        new_width = int(height * target)
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = int(width / target)
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def to_webp(raw: bytes, crop: str | None) -> tuple[bytes, tuple[int, int]]:
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    if crop:
        image = crop_to_ratio(image, crop)
    if image.width > MAX_WIDTH:
        # int(), а не round(): та же функция округления, что и в
        # crop_to_ratio выше, — единое правило на модуль, чтобы никто не
        # «поправил» одну из них по вкусу и не рассинхронизировал результат.
        image = image.resize((MAX_WIDTH, int(image.height * MAX_WIDTH / image.width)),
                             Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=WEBP_QUALITY, method=6)
    return buffer.getvalue(), image.size


class ImageGenerator:
    def __init__(self, base_url: str, api_key: str, model: str, max_retries: int = 2,
                 backoff: float = 5.0):
        self.url = base_url.rstrip("/") + "/images"
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.backoff = backoff

    def generate(self, prompt: str, size: str, quality: str,
                 crop: str | None) -> ImageResult:
        payload = {
            "model": self.model, "prompt": prompt, "n": 1,
            "size": size, "quality": quality, "output_format": "webp",
        }
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            started = time.time()
            try:
                response = requests.post(self.url, headers=headers, json=payload,
                                         timeout=TIMEOUT)
            except Exception as exc:
                last_error = str(exc)[:200]
            else:
                if response.ok:
                    try:
                        body = response.json()
                        raw = base64.b64decode(body["data"][0]["b64_json"])
                        data, image_size = to_webp(raw, crop)
                    except Exception as exc:
                        # Мусор в теле 200-го ответа не лечится повтором — тот
                        # же аргумент, что и с неретраибельными ошибками в
                        # app/ai/text.py: повторный запрос с высокой
                        # вероятностью вернёт тот же мусор, а не другой ответ.
                        raise ImageError(
                            f"RouterAI images вернул 200, но тело не "
                            f"разобрать ({type(exc).__name__}): {exc}") from exc
                    return ImageResult(
                        data=data, size=image_size,
                        cost=float(body.get("usage", {}).get("cost") or 0.0),
                        seconds=round(time.time() - started),
                    )
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if attempt < self.max_retries:
                time.sleep(self.backoff * attempt)

        raise ImageError(f"RouterAI images не ответил после {self.max_retries} попыток: "
                         f"{last_error}")
```

- [x] **Step 5: Реализация watermark.py**

`execution/backend/app/ai/watermark.py`:

```python
"""Наложение водяного знака сайта на контентные картинки.

Знак ставится в правый нижний угол, небольшой, с отступом от обоих краёв;
на обложку статьи знак НЕ накладывается — это витрина, а не иллюстрация
внутри текста.

Пропорции заданы владельцем по образцу готовой картинки: знак занимает
примерно одну десятую ширины кадра и не касается краёв. Это осознанно
скромно — знак помечает авторство, а не борется за внимание с содержимым
кадра. Числа ниже закреплены тестами (`tests/test_ai_watermark.py`):
проверяется и доля ширины, и наличие отступов, поэтому «чуть покрупнее»
не пройдёт молча.
"""

from __future__ import annotations

import io

from PIL import Image

MARK_WIDTH_FRACTION = 0.11   # доля ширины кадра, которую занимает знак
MARGIN_FRACTION = 0.045      # отступ от краёв, доля ширины кадра
OPACITY = 0.75


def apply_watermark(image_bytes: bytes, watermark_bytes: bytes) -> bytes:
    """Возвращает webp с наложенным знаком. Пустой знак — картинка без изменений."""
    if not watermark_bytes:
        return image_bytes

    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    mark = Image.open(io.BytesIO(watermark_bytes)).convert("RGBA")

    # Знак масштабируется по ширине кадра, а не берётся как есть: файлы знаков
    # у разных сайтов разного размера, и знак шире картинки обрезался бы краем.
    target_width = max(1, int(base.width * MARK_WIDTH_FRACTION))
    target_height = max(1, round(mark.height * target_width / mark.width))
    mark = mark.resize((target_width, target_height), Image.LANCZOS)

    if OPACITY < 1.0:
        alpha = mark.getchannel("A").point(lambda v: int(v * OPACITY))
        mark.putalpha(alpha)

    margin = int(base.width * MARGIN_FRACTION)
    position = (base.width - target_width - margin, base.height - target_height - margin)

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(mark, position)
    merged = Image.alpha_composite(base, layer).convert("RGB")

    buffer = io.BytesIO()
    merged.save(buffer, "WEBP", quality=82, method=6)
    return buffer.getvalue()
```

- [x] **Step 6: Запустить тесты, убедиться что проходят**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_images.py tests/test_ai_watermark.py -v`
Expected: PASS — 18 passed

- [x] **Step 7: Commit**

```bash
git add execution/backend/app/ai execution/backend/tests/test_ai_images.py execution/backend/tests/test_ai_watermark.py
git commit -m "feat: генерация картинок RouterAI, кроп и водяной знак"
```

- [x] **Step 8: Замечания ревью — округление, таймауты, разбор ответа**

Ревью нашло три проблемы (полное исследование — в отчёте по Task 8):

1. `to_webp` округляла через `round()`, а `crop_to_ratio` — через `int()`;
   для `png_bytes(2400, 1600)` это давало `1067` вместо ожидаемых тестом
   `1066`. Приведено к `int()` — одна функция округления на модуль (код
   выше уже отражает исправление).
2. `TIMEOUT=420` и `max_retries=3` давали до ≈1275 с на одну картинку —
   втрое больше `ARTICLE_TIME_BUDGET_SECONDS` (Task 18). Приведено к
   `TIMEOUT=180`, `max_retries=2`: худший случай ≈365 с, симметрично
   текстовому клиенту (Task 7). Код выше уже отражает исправление.
3. Разбор тела успешного (200) ответа (`response.json()`,
   `base64.b64decode`, `to_webp`) не был обёрнут в `try/except` — мусор в
   теле ответа (`UnidentifiedImageError`, `KeyError`, `binascii.Error`,
   обрезанный файл) вылетал из `generate()` сырым исключением мимо
   `ImageError` и мимо перечня перехватываемых исключений в
   `ArticleBuilder.build()` (Task 16). Обёрнуто в `ImageError` без ретрая —
   мусор в ответе не лечится повтором, тот же аргумент, что и с
   неретраибельными ошибками в `app/ai/text.py`. Код и тесты выше уже
   отражают исправление.

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_images.py tests/test_ai_watermark.py -v`
Expected: PASS — 18 passed

```bash
git add execution/backend/app/ai execution/backend/tests/test_ai_images.py execution/backend/tests/test_ai_watermark.py execution/backend/app/seed.py execution/backend/app/api/admin_settings.py execution/backend/tests/test_api_admin_settings.py orchestration/2026-08-04-plan1-core-and-articles.md
git commit -m "fix: замечания ревью по картинкам — округление, таймауты, разбор ответа, границы image_workers"
```

---

## Фаза 2 — Сайты

### Task 9: Модель сайта

**Files:**
- Create: `execution/backend/app/models/site.py`
- Modify: `execution/backend/app/models/__init__.py` (зарегистрировать `Site`)
- Test: `execution/backend/tests/test_models_site.py`

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_models_site.py`:

```python
import pytest
from sqlalchemy import Integer
from sqlalchemy.exc import IntegrityError

from app.models.site import Site


def test_site_minimal_fields():
    site = Site(name="Стройбаза Самара", domain="stroybaza-samara.ru",
                base_url="https://stroybaza-samara.ru", api_token_enc="enc")
    assert site.__tablename__ == "sites"
    assert site.domain == "stroybaza-samara.ru"


def test_site_article_settings():
    """Раздел задаётся id родительской страницы; её url подтягивается синхронизацией,
    руками не вводится. `/blog/` — частный случай, а не требование."""
    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                publish_target="pages", articles_parent_id=25,
                articles_url_prefix="/poleznye-stati/", reference_article_id=312)
    assert site.articles_parent_id == 25
    assert site.articles_url_prefix == "/poleznye-stati/"


def test_site_reference_cache_fields():
    """Число картинок не настраивается — оно равно числу <img> в эталоне."""
    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                reference_article_id=312, reference_html="<p>x</p><img><img>",
                reference_images=2)
    assert site.reference_images == 2
    assert not hasattr(Site, "images_per_article")
    assert not hasattr(Site, "article_template_html")


def test_site_content_profile():
    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                site_description="Строительная база в Самаре, аудитория — частные "
                                 "застройщики",
                tone_of_voice="практичный, без рекламных обещаний")
    assert "Самаре" in site.site_description
    assert "практичный" in site.tone_of_voice


def test_site_builder_teaser_taxonomy():
    """category/city/location — это карточки-тизеры каталога строителей
    (addresses-services), к обложке статьи отношения не имеют."""
    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                teaser_category_id=3, teaser_city_id=2, teaser_location_id=1)
    assert (site.teaser_category_id, site.teaser_city_id, site.teaser_location_id) == (3, 2, 1)


def test_reference_images_is_integer():
    """Число картинок равно числу <img> в эталоне — строка здесь молча
    сломала бы арифметику при сборке статьи."""
    assert isinstance(Site.__table__.c.reference_images.type, Integer)


def test_domain_is_unique(db_session):
    db_session.add(Site(name="A", domain="dup.ru", base_url="https://dup.ru",
                         api_token_enc="e"))
    db_session.commit()

    db_session.add(Site(name="B", domain="dup.ru", base_url="https://dup.ru",
                         api_token_enc="e"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_domain_is_normalized_on_assignment():
    """DNS регистр не различает, а колонка — различает: без нормализации
    example.ru и Example.ru завелись бы как два разных сайта."""
    site = Site(name="X", domain="  Example.RU  ", base_url="https://example.ru",
                api_token_enc="e")
    assert site.domain == "example.ru"


def test_normalized_domain_collides_with_existing(db_session):
    """Нормализация в модели — на любом пути записи, а не только там, где о ней
    вспомнили: разный регистр не должен давать два сайта на один домен."""
    db_session.add(Site(name="A", domain="example.ru", base_url="https://example.ru",
                         api_token_enc="e"))
    db_session.commit()

    db_session.add(Site(name="B", domain="Example.ru", base_url="https://example.ru",
                         api_token_enc="e"))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_site.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.site'`

- [x] **Step 3: Реализация**

`execution/backend/app/models/site.py`:

```python
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
```

- [x] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_site.py -v`
Expected: PASS — 9 passed

- [x] **Step 5: Миграция**

Добавь `Site` в `app/models/__init__.py` (реестр моделей, см. Task 2, Step 3) —
`alembic/env.py` и `tests/conftest.py` подхватят её через `import app.models` без
собственных правок. Затем:

Run:
```bash
cd execution && docker compose run --rm backend alembic revision --autogenerate -m "sites"
docker compose run --rm backend alembic upgrade head
```
Expected: `Running upgrade <prev> -> <hash>, sites`

- [x] **Step 6: Commit**

```bash
git add execution/backend/app/models/site.py execution/backend/app/models/__init__.py \
        execution/backend/alembic execution/backend/tests/test_models_site.py \
        orchestration/2026-08-04-plan1-core-and-articles.md
git commit -m "feat: модель сайта"
```

---

### Task 10: Клиент API целевого сайта

Порт `execution/filemanager.py` плюс операции со `staticpages`, вынесенные из
`execution/articles/publish_articles.py`.

**Files:**
- Create: `execution/backend/app/sites/__init__.py`
- Create: `execution/backend/app/sites/client.py`
- Test: `execution/backend/tests/test_sites_client.py`

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_sites_client.py`:

```python
import json

import pytest

from app.sites.client import SiteAPIError, SiteClient, slugify


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", json_error=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {}
        self._json_error = json_error
        self.text = text

    def json(self):
        if self._json_error:
            # requests.Response.json() поднимает подкласс ValueError
            # (json.JSONDecodeError или requests.exceptions.JSONDecodeError,
            # который сам от него унаследован) — воспроизводим это же исключение.
            raise json.JSONDecodeError("Expecting value", self.text or "", 0)
        return self._payload


def test_slugify_transliterates_and_limits():
    assert slugify("Как выбрать фундамент для дома") == "kak-vybrat-fundament-dlya-doma"
    assert len(slugify("а" * 200, limit=70)) == 70


def test_slugify_strips_punctuation():
    assert slugify("Дом: 5 ошибок!") == "dom-5-oshibok"


def test_list_section_pages_follows_pagination(monkeypatch):
    pages = {
        1: {"results": [{"id": 1, "title": "Статья A", "url": "/blog/a/"},
                        {"id": 2, "title": "О компании", "url": "/about/"}],
            "next": "?page=2"},
        2: {"results": [{"id": 3, "title": "Статья B", "url": "/blog/b/"}], "next": None},
    }
    seen = []

    def fake_get(url, **kwargs):
        page = 2 if "page=2" in url else 1
        seen.append(page)
        return FakeResponse(200, pages[page])

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    client = SiteClient("https://x.ru", "token")
    result = client.list_section_pages("/blog/")
    assert seen == [1, 2]
    assert [p["title"] for p in result] == ["Статья A", "Статья B"]


def test_create_page_sends_draft_payload(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, json=kwargs["json"])
        return FakeResponse(201, {"id": 77, "url": "/blog/test/"})

    monkeypatch.setattr("app.sites.client.requests.post", fake_post)
    client = SiteClient("https://x.ru", "token")
    created = client.create_page(title="Тест", url="/blog/test/", html="<p>x</p>",
                                 parent_id=25, meta_description="d", meta_keywords="k")
    assert created["id"] == 77
    assert captured["json"]["published"] is False
    assert captured["json"]["parent"] == 25
    assert captured["json"]["wide_view"] is True
    assert captured["json"]["use_editor"] is False


def test_create_page_strips_html_comments(monkeypatch):
    captured = {}
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda url, **kw: (captured.update(kw["json"]),
                                           FakeResponse(201, {"id": 1, "url": "/blog/x/"}))[1])
    SiteClient("https://x.ru", "t").create_page(
        title="T", url="/blog/x/", html="<p>a</p><!-- служебный -->", parent_id=25)
    assert "служебный" not in captured["text"]


def test_upload_file_builds_predictable_path(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, data=kwargs["data"])
        return FakeResponse(200, {}, text="Success")

    monkeypatch.setattr("app.sites.client.requests.post", fake_post)
    client = SiteClient("https://x.ru", "token")
    path = client.upload_file(b"data", "article_1-1.webp", "uploads/article-img/")
    # Путь не приходит в ответе — он предсказуем, потому что коллизия имени
    # в filemanager означает перезапись, а не суффикс.
    assert path == "/media/uploads/article-img/article_1-1.webp"
    assert captured["data"]["upload_to"] == "uploads/article-img/"


def test_upload_uses_token_header_not_stroyker_key(monkeypatch):
    """X-STROYKER-KEY из документации даёт 403 — рабочая авторизация
    обычным Token."""
    captured = {}
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda url, **kw: (captured.update(kw["headers"]),
                                           FakeResponse(200, {}, "Success"))[1])
    SiteClient("https://x.ru", "tok").upload_file(b"d", "a.webp", "uploads/article-img/")
    assert captured["Authorization"] == "Token tok"
    assert "X-STROYKER-KEY" not in captured


def test_set_page_cover_patches_teaser_image(monkeypatch):
    captured = {}

    def fake_patch(url, **kwargs):
        captured.update(url=url, files=kwargs["files"])
        return FakeResponse(200, {"teaser_image": "/media/staticpages/images/cover.webp"})

    monkeypatch.setattr("app.sites.client.requests.patch", fake_patch)
    client = SiteClient("https://x.ru", "token")
    result = client.set_page_cover(77, b"img", "cover.webp")
    assert captured["url"] == "https://x.ru/api/v1/staticpages/77/"
    assert "teaser_image" in captured["files"]
    assert result == "/media/staticpages/images/cover.webp"


def test_error_response_raises(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda *a, **kw: FakeResponse(403, text="Forbidden"))
    with pytest.raises(SiteAPIError, match="403"):
        SiteClient("https://x.ru", "bad").create_page(
            title="T", url="/blog/x/", html="<p>a</p>", parent_id=25)


# --- SiteAPIError несёт статус ответа: 404 в исключении при HTTP-ошибке,
# None при ошибке разбора тела (сайт вернул 200 с мусором) или сетевом сбое. ---

def test_error_status_code_is_exposed_on_http_error(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda *a, **kw: FakeResponse(404, text="Not Found"))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").create_page(
            title="T", url="/blog/x/", html="<p>a</p>", parent_id=25)
    assert exc_info.value.status_code == 404


# --- «200 OK с мусором внутри»: HTML прокси, обрезанный JSON или пустое тело
# при успешном статусе не должны долетать до вызывающего голым
# json.JSONDecodeError — только SiteAPIError с status_code=None. Тот же класс
# дефекта уже закрыт в app/ai/images.py для ответов RouterAI. ---

_BAD_BODIES = pytest.mark.parametrize("bad_text", [
    "<html><body>Please log in</body></html>",   # HTML вместо JSON
    '{"id": 1, "url": "/blog/x/"',                # обрезанный JSON
    "",                                            # пустое тело
], ids=["html", "truncated-json", "empty"])


@_BAD_BODIES
def test_list_section_pages_rejects_invalid_json(monkeypatch, bad_text):
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda *a, **kw: FakeResponse(200, text=bad_text, json_error=True))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").list_section_pages("/blog/")
    assert exc_info.value.status_code is None


@_BAD_BODIES
def test_get_page_rejects_invalid_json(monkeypatch, bad_text):
    monkeypatch.setattr("app.sites.client.requests.get",
                        lambda *a, **kw: FakeResponse(200, text=bad_text, json_error=True))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").get_page(77)
    assert exc_info.value.status_code is None


@_BAD_BODIES
def test_create_page_rejects_invalid_json(monkeypatch, bad_text):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda *a, **kw: FakeResponse(200, text=bad_text, json_error=True))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").create_page(
            title="T", url="/blog/x/", html="<p>a</p>", parent_id=25)
    assert exc_info.value.status_code is None


@_BAD_BODIES
def test_set_page_cover_rejects_invalid_json(monkeypatch, bad_text):
    monkeypatch.setattr("app.sites.client.requests.patch",
                        lambda *a, **kw: FakeResponse(200, text=bad_text, json_error=True))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").set_page_cover(77, b"img", "cover.webp")
    assert exc_info.value.status_code is None


# --- upload_file обязан поднимать SiteAPIError на ошибочном статусе — раньше
# проверялось только для create_page. ---

@pytest.mark.parametrize("status", [413, 500])
def test_upload_file_raises_on_error_status(monkeypatch, status):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda *a, **kw: FakeResponse(status, text="oops"))
    with pytest.raises(SiteAPIError) as exc_info:
        SiteClient("https://x.ru", "t").upload_file(b"d", "a.webp", "uploads/article-img/")
    assert exc_info.value.status_code == status


# --- таймауты: все методы обязаны передавать числовой таймаут в requests,
# а не None (бесконечное ожидание съедает слот воркера часами). ---

def test_all_methods_use_numeric_timeout(monkeypatch):
    captured = []

    def fake_get(url, **kwargs):
        captured.append(kwargs.get("timeout"))
        return FakeResponse(200, {"results": [], "next": None})

    def fake_post(url, **kwargs):
        captured.append(kwargs.get("timeout"))
        return FakeResponse(201, {"id": 1, "url": "/blog/x/"})

    def fake_patch(url, **kwargs):
        captured.append(kwargs.get("timeout"))
        return FakeResponse(200, {"teaser_image": "/media/x.webp"})

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    monkeypatch.setattr("app.sites.client.requests.post", fake_post)
    monkeypatch.setattr("app.sites.client.requests.patch", fake_patch)

    client = SiteClient("https://x.ru", "token")
    client.list_section_pages("/blog/")
    client.get_page(1)
    client.create_page(title="T", url="/blog/x/", html="<p>a</p>", parent_id=25)
    client.set_page_cover(1, b"img", "cover.webp")
    client.upload_file(b"data", "a.webp", "uploads/article-img/")

    assert len(captured) == 5
    for value in captured:
        assert isinstance(value, (int, float)) and value > 0


def test_timeout_and_upload_timeout_are_independently_configurable(monkeypatch):
    """set_page_cover/upload_file раньше игнорировали self.timeout из
    конструктора и жёстко использовали 120 — несогласованность, а не
    решение. Теперь у загрузки свой явный параметр конструктора."""
    captured = {}

    def fake_get(url, **kwargs):
        captured["get"] = kwargs.get("timeout")
        return FakeResponse(200, {"results": [], "next": None})

    def fake_post(url, **kwargs):
        captured["upload"] = kwargs.get("timeout")
        return FakeResponse(200, {}, text="Success")

    monkeypatch.setattr("app.sites.client.requests.get", fake_get)
    monkeypatch.setattr("app.sites.client.requests.post", fake_post)

    client = SiteClient("https://x.ru", "token", timeout=45, upload_timeout=200)
    client.list_section_pages("/blog/")
    client.upload_file(b"d", "a.webp", "uploads/article-img/")

    assert captured["get"] == 45
    assert captured["upload"] == 200
```

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_sites_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sites'`

- [x] **Step 3: Реализация**

`execution/backend/app/sites/__init__.py` — пустой файл.

`execution/backend/app/sites/client.py`:

```python
"""Клиент API целевого сайта: страницы, загрузка файлов, обложка.

Порт execution/filemanager.py и execution/articles/publish_articles.py —
токен приходит параметром из БД, а не из .env.

Проверено на stroybaza-samara.ru:
- авторизация везде `Authorization: Token ...`; X-STROYKER-KEY из доков даёт 403;
- upload_to — путь относительно каталога «Медиа» (без 'media/' и ведущего слэша),
  несуществующая подпапка создаётся автоматически;
- коллизия имени в filemanager = перезапись без суффикса, поэтому путь строим сами;
- у списка staticpages пагинация `?page=N`, фильтры ?parent= и ?search= игнорируются,
  раздел вычленяется по префиксу url;
- teaser_image — ImageField страницы, строкой не задаётся, только multipart.

Замечания ревью (сверка с рабочими скриптами и app/ai/images.py):
- `SiteAPIError.status_code` хранит HTTP-код ответа (None для ошибок разбора
  тела и сетевых сбоев) — вызывающий код (Task 11/18) решает по нему, есть ли
  смысл повторить запрос: 5xx и сетевые таймауты — да, 400/401/403/404/413 —
  нет (та же граница, что и для RouterAI в app/ai/text.py). Сам клиент
  ретраи не делает — это ответственность вызывающего кода;
- тело успешного ответа не гарантированно JSON: прокси, страница логина или
  обрыв соединения посреди тела отдают 200 с мусором. `.json()` всегда
  обёрнут — иначе наружу летит голый json.JSONDecodeError вместо
  SiteAPIError (тот же класс дефекта, что уже закрыт в app/ai/images.py для
  ответов RouterAI);
- `timeout` всегда берётся из атрибутов клиента, а не зашит числом в теле
  метода: `self.timeout` — для чтения/создания страниц, `self.upload_timeout`
  — для загрузки файлов и обложки (файлы крупнее, дефолт больше).
"""

from __future__ import annotations

import io
import mimetypes
import re

import requests

STATICPAGES_PATH = "/api/v1/staticpages/"
ARTICLES_PATH = "/api/v1/articles/"
FILEMANAGER_PATH = "/api/v1/filemanager/"

ARTICLE_IMG_DIR = "uploads/article-img/"
SERVICE_IMG_DIR = "uploads/service-img/"

SLUG_LIMIT_PAGES = 70     # существующие url на сайтах обрезаны примерно здесь
SLUG_LIMIT_ARTICLES = 50

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class SiteAPIError(RuntimeError):
    """status_code — HTTP-код ответа сайта; None для ошибок разбора тела
    (сайт вернул 200, но не JSON) и для сетевых сбоев ниже уровня HTTP."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def slugify(text: str, limit: int = 60) -> str:
    result = "".join(_TRANSLIT.get(c, c) for c in text.lower())
    result = re.sub(r"[^a-z0-9]+", "-", result)
    return result.strip("-")[:limit].strip("-")


def strip_html_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


class SiteClient:
    def __init__(self, base_url: str, token: str, timeout: int = 60,
                upload_timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.upload_timeout = upload_timeout

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}", "Accept": "application/json"}

    def _check(self, response, what: str):
        if not response.ok:
            raise SiteAPIError(f"{what}: HTTP {response.status_code}: {response.text[:300]}",
                               status_code=response.status_code)
        return response

    def _json(self, response, what: str):
        """Тело успешного ответа не гарантированно JSON: прокси, страница
        логина или обрыв соединения посреди тела отдают 200 с мусором. Без
        обёртки сюда долетает голый json.JSONDecodeError вместо SiteAPIError —
        тот же класс дефекта, что уже закрыт в app/ai/images.py."""
        try:
            return response.json()
        except ValueError as exc:
            raise SiteAPIError(f"{what}: сайт вернул не JSON: {response.text[:300]}") from exc

    # --- страницы ---

    def list_section_pages(self, url_prefix: str) -> list[dict]:
        """Все страницы раздела. Фильтр ?parent= сайт игнорирует, поэтому
        раздел отбирается по префиксу url на нашей стороне."""
        pages, page_number = [], 1
        while True:
            response = self._check(
                requests.get(f"{self.base_url}{STATICPAGES_PATH}?page={page_number}",
                             headers=self._headers, timeout=self.timeout),
                "список страниц")
            body = self._json(response, "список страниц")
            pages += [item for item in body.get("results", [])
                      if (item.get("url") or "").startswith(url_prefix)]
            if not body.get("next"):
                return pages
            page_number += 1

    def get_page(self, page_id: int) -> dict:
        response = self._check(
            requests.get(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                         headers=self._headers, timeout=self.timeout),
            f"страница {page_id}")
        return self._json(response, f"страница {page_id}")

    def create_page(self, title: str, url: str, html: str, parent_id: int | None,
                    meta_description: str = "", meta_keywords: str = "") -> dict:
        payload = {
            "title": title,
            "url": url,
            "text": strip_html_comments(html).strip(),
            "published": False,       # черновик: публикует менеджер вручную
            "parent": parent_id,
            "wide_view": True,
            "use_editor": False,
            "meta_description": meta_description,
            "meta_keywords": meta_keywords,
        }
        response = self._check(
            requests.post(f"{self.base_url}{STATICPAGES_PATH}", json=payload,
                          headers={**self._headers, "Content-Type": "application/json"},
                          timeout=self.timeout),
            "создание страницы")
        return self._json(response, "создание страницы")

    def set_page_cover(self, page_id: int, image_bytes: bytes, filename: str) -> str:
        """teaser_image — ImageField страницы: путём-строкой не задаётся (400),
        только multipart прямо в поле."""
        ctype = mimetypes.guess_type(filename)[0] or "image/webp"
        response = self._check(
            requests.patch(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                           headers=self._headers,
                           files={"teaser_image": (filename, io.BytesIO(image_bytes), ctype)},
                           timeout=self.upload_timeout),
            "загрузка обложки")
        return self._json(response, "загрузка обложки").get("teaser_image", "")

    # --- файлы ---

    def upload_file(self, data: bytes, filename: str, upload_to: str) -> str:
        """Возвращает предсказуемый путь /media/{upload_to}{filename}:
        сам ответ пути не содержит, а коллизия имени означает перезапись."""
        upload_to = upload_to.strip("/") + "/"
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._check(
            requests.post(f"{self.base_url}{FILEMANAGER_PATH}",
                          headers=self._headers,
                          files={"file": (filename, io.BytesIO(data), ctype)},
                          data={"upload_to": upload_to},
                          timeout=self.upload_timeout),
            "загрузка файла")
        return f"/media/{upload_to}{filename}"
```

- [x] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_sites_client.py -v`
Expected: PASS — 26 passed

- [x] **Step 5: Commit**

```bash
git add execution/backend/app/sites execution/backend/tests/test_sites_client.py
git commit -m "feat: клиент API целевого сайта"
```

- [x] **Step 6: Замечания ревью — разбор ответа, код статуса, таймауты**

Ревью нашло четыре проблемы (полное исследование — в отчёте по Task 10):

1. Тело успешного (200) ответа не проверялось на JSON — HTML от прокси или
   обрыв соединения посреди тела вылетали из `list_section_pages`,
   `get_page`, `create_page` и `set_page_cover` сырым `json.JSONDecodeError`
   вместо `SiteAPIError`. Тот же класс дефекта уже закрыт в
   `app/ai/images.py` для ответов RouterAI. Обёрнуто в `_json()`. Код и
   тесты выше уже отражают исправление.
2. `SiteAPIError` не хранил код ответа — вызывающий код, которому нужно
   повторять 5xx, но не 400/401/403/404/413, был бы вынужден разбирать
   строку регуляркой. Добавлен атрибут `status_code` (`None` для сетевых
   сбоев и ошибок разбора тела); граница ретраев вписана в Task 11. Код и
   тесты выше уже отражают исправление.
3. `set_page_cover` и `upload_file` игнорировали `self.timeout` из
   конструктора и жёстко использовали 120 — несогласованность, а не
   решение; мутация «заменить все таймауты на `None`» проходила
   незамеченной. Добавлен отдельный параметр конструктора
   `upload_timeout` (дефолт 120), все методы теперь берут таймаут из
   атрибутов клиента. Код и тесты выше уже отражают исправление.
4. `upload_file` не был покрыт тестом на ошибочный статус — проверялось
   только создание страницы. Добавлены тесты на 413 и 500.

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_sites_client.py -v`
Expected: PASS — 26 passed

```bash
git add execution/backend/app/sites execution/backend/tests/test_sites_client.py orchestration/2026-08-04-plan1-core-and-articles.md
git commit -m "fix: замечания ревью по клиенту сайта — разбор ответа, код статуса, таймауты"
```

---

### Task 11: Синхронизация эталона и API сайтов

**Требование по ретраям (замечание ревью Task 10):** `SiteClient` сам не
повторяет запросы к сайту — это ответственность вызывающего кода (здесь, в
`open_client`/`sync_site`, и в Task 18). Есть смысл повторять только 5xx и
сетевые таймауты; 400, 401, 403, 404, 413 повторять нельзя — повтор с тем же
запросом гарантированно даёт тот же результат (неверный токен, отсутствующая
родительская страница, файл слишком велик, некорректные данные), а время и
попытки тратятся впустую. Граница ретраев — `exc.status_code` из
`SiteAPIError` (Task 10): `None` (сетевой сбой или сайт вернул не JSON) или
`>= 500` → повторять, иначе — нет. Решение зеркалит `_NON_RETRYABLE` в
`app/ai/text.py` (Task 7), где по той же причине не повторяются
`AuthenticationError`, `PermissionDeniedError`, `BadRequestError` и
`NotFoundError` от RouterAI.

**Files:**
- Create: `execution/backend/app/sites/reference.py`
- Create: `execution/backend/app/api/sites.py`
- Create: `execution/backend/app/api/admin_sites.py`
- Modify: `execution/backend/app/main.py`
- Test: `execution/backend/tests/test_sites_reference.py`
- Test: `execution/backend/tests/test_api_sites.py`

- [x] **Step 1: Написать падающий тест на синхронизацию**

`execution/backend/tests/test_sites_reference.py`:

```python
from types import SimpleNamespace

import pytest

from app.sites.reference import ReferenceError, count_images, sync_site_reference


def test_count_images_counts_img_tags():
    html = "<p>Текст</p><img src='a.webp'><p>Ещё</p><img src='b.webp'/>"
    assert count_images(html) == 2


def test_count_images_ignores_case_and_attributes():
    assert count_images('<IMG SRC="a.webp" class="x">') == 1


def test_count_images_ignores_img_inside_comment():
    """Закомментированная картинка не рендерится — считать её нельзя,
    иначе сгенерируем лишний кадр и заплатим за него."""
    assert count_images("<img src='a.webp'><!-- <img src='b.webp'> -->") == 1


def test_count_images_on_empty_html():
    assert count_images("") == 0


class FakeClient:
    def __init__(self, parent_url="/poleznye-stati/", reference_html="<img><img>"):
        self.parent_url = parent_url
        self.reference_html = reference_html
        self.requested = []

    def get_page(self, page_id):
        self.requested.append(page_id)
        if page_id == 25:
            return {"id": 25, "url": self.parent_url, "text": "<p>раздел</p>"}
        return {"id": page_id, "url": "/poleznye-stati/etalon/",
                "text": self.reference_html}

    def list_section_pages(self, prefix):
        return [{"id": 9, "title": "Старая", "url": prefix + "staraya/"}]


@pytest.fixture
def site(db_session):
    from app.models.site import Site

    row = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
               articles_parent_id=25, reference_article_id=312)
    db_session.add(row)
    db_session.commit()
    return row


def test_sync_derives_url_prefix_from_parent(db_session, site):
    """Префикс не вводится руками: он берётся с самой родительской страницы,
    поэтому не может разъехаться с тем, что на сайте."""
    sync_site_reference(db_session, site, FakeClient())
    assert site.articles_url_prefix == "/poleznye-stati/"


def test_sync_caches_reference_html_and_image_count(db_session, site):
    sync_site_reference(db_session, site, FakeClient(reference_html="<p>t</p><img><img><img>"))
    assert site.reference_images == 3
    assert "<p>t</p>" in site.reference_html
    assert site.reference_synced_at is not None


def test_sync_requires_parent_id(db_session, site):
    site.articles_parent_id = None
    db_session.commit()
    with pytest.raises(ReferenceError, match="родительск"):
        sync_site_reference(db_session, site, FakeClient())


def test_sync_requires_reference_article(db_session, site):
    site.reference_article_id = None
    db_session.commit()
    with pytest.raises(ReferenceError, match="Эталонная"):
        sync_site_reference(db_session, site, FakeClient())


def test_sync_rejects_reference_without_images(db_session, site):
    """Эталон без единой картинки означает, что статьи пойдут без иллюстраций —
    это почти всегда ошибка выбора эталона, а не осознанное решение."""
    with pytest.raises(ReferenceError, match="ни одной картинки"):
        sync_site_reference(db_session, site, FakeClient(reference_html="<p>только текст</p>"))


def test_sync_failure_does_not_clobber_previous_cache(db_session, site):
    """Отказ синхронизации (эталон без картинок) не должен стирать кеш от
    прошлой успешной синхронизации — иначе один плохой запуск оставляет сайт
    вовсе без эталона, и статьи станет не по чему собирать."""
    sync_site_reference(db_session, site, FakeClient(reference_html="<p>t</p><img><img>"))
    old_prefix = site.articles_url_prefix
    old_html = site.reference_html
    old_images = site.reference_images
    old_synced_at = site.reference_synced_at
    assert old_images == 2

    with pytest.raises(ReferenceError, match="ни одной картинки"):
        sync_site_reference(db_session, site, FakeClient(reference_html="<p>только текст</p>"))

    assert site.articles_url_prefix == old_prefix
    assert site.reference_html == old_html
    assert site.reference_images == old_images
    assert site.reference_synced_at == old_synced_at
```

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_sites_reference.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sites.reference'`

- [x] **Step 3: Реализация синхронизации**

`execution/backend/app/sites/reference.py`:

```python
"""Синхронизация карточки сайта с самим сайтом: раздел статей и эталонная статья.

Эталон — единственный источник разметки, отдельного HTML-шаблона в сервисе нет.
Он кешируется в карточке: при генерации статьи за ним на сайт не ходим.

Кеш экономит один HTTP-запрос на статью — это мелочь. Основная цена эталона в том,
что его HTML уходит во входные токены КАЖДОГО запроса article_body, и кеширование
этого не меняет. Если счёт за текст станет заметным, сокращать надо объём эталона
(скелет разметки вместо полного HTML), а не число обращений.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models.site import Site

# Комментарии вырезаются перед подсчётом: закомментированная картинка не
# рендерится, а лишний сгенерированный кадр стоит денег.
_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_IMG = re.compile(r"<img\b", re.IGNORECASE)


class ReferenceError(RuntimeError):
    pass


def count_images(html: str) -> int:
    """Сколько <img> в разметке — столько картинок и генерируется для статьи."""
    return len(_IMG.findall(_COMMENTS.sub("", html or "")))


def sync_site_reference(db: Session, site: Site, client, commit: bool = True) -> None:
    """Тянет раздел и эталон, заполняет кеш карточки. Бросает ReferenceError
    с человеческим текстом — вызывающий показывает его администратору.

    `commit=False` — для вызывающих, которым нужен один коммит на несколько
    шагов (`sync_site` в app/api/admin_sites.py: эталон и список страниц
    раздела пишутся одной транзакцией, чтобы отказ на втором шаге не оставлял
    в БД наполовину обновлённую карточку). По умолчанию коммитит сама — так
    же, как `SettingsService.set`/`set_secret` (app/settings/service.py)."""
    if not site.articles_parent_id:
        raise ReferenceError("не задан id родительской страницы раздела статей")
    if not site.reference_article_id:
        raise ReferenceError("Эталонная статья не задана — без неё разметку взять неоткуда")

    parent = client.get_page(site.articles_parent_id)
    prefix = parent.get("url") or ""
    if not prefix:
        raise ReferenceError(f"у страницы {site.articles_parent_id} нет url — "
                             f"это точно раздел статей?")

    reference = client.get_page(site.reference_article_id)
    html = reference.get("text") or reference.get("body") or ""
    images = count_images(html)
    if images == 0:
        raise ReferenceError("в эталонной статье ни одной картинки — статьи получатся "
                             "без иллюстраций; проверь, тот ли это id")

    site.articles_url_prefix = prefix if prefix.endswith("/") else prefix + "/"
    site.reference_html = html
    site.reference_images = images
    site.reference_synced_at = utcnow()
    if commit:
        db.commit()
```

- [x] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_sites_reference.py -v`
Expected: PASS — 10 passed

- [x] **Step 5: Написать падающий тест на API сайтов**

`execution/backend/tests/test_api_sites.py`:

```python
import pytest


@pytest.fixture
def site_payload():
    return {
        "name": "Стройбаза Самара",
        "domain": "stroybaza-samara.ru",
        "base_url": "https://stroybaza-samara.ru",
        "api_token": "real-secret-token-value",
        "publish_target": "pages",
        "articles_parent_id": 25,
        "reference_article_id": 312,
        "site_description": "Строительная база в Самаре, аудитория — частные застройщики",
        "tone_of_voice": "практичный, без рекламных обещаний",
        "image_style_prompt": "реалистичное фото стройки",
        "cover_mode": "prompt",
        "cover_style_prompt": "широкая обложка",
        "teaser_category_id": 3,
        "teaser_city_id": 2,
        "teaser_location_id": 1,
    }


def test_manager_cannot_create_site(manager_client, site_payload):
    assert manager_client.post("/api/admin/sites", json=site_payload).status_code == 403


def test_admin_creates_site(admin_client, site_payload):
    resp = admin_client.post("/api/admin/sites", json=site_payload)
    assert resp.status_code == 200
    assert resp.json()["domain"] == "stroybaza-samara.ru"


def test_token_is_masked_in_response(admin_client, site_payload):
    created = admin_client.post("/api/admin/sites", json=site_payload).json()
    assert created["api_token"] == "rea...alue"


def test_token_is_stored_encrypted(admin_client, db_session, site_payload):
    admin_client.post("/api/admin/sites", json=site_payload)
    from app.models.site import Site

    site = db_session.query(Site).first()
    assert "real-secret-token-value" not in site.api_token_enc


def test_empty_token_on_update_keeps_current(admin_client, db_session, site_payload):
    from app.config import config
    from app.models.site import Site
    from app.settings.crypto import decrypt

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    admin_client.put(f"/api/admin/sites/{site_id}",
                     json={**site_payload, "api_token": "", "tone_of_voice": "сухой"})

    site = db_session.get(Site, site_id)
    assert decrypt(site.api_token_enc, config.encryption_key) == "real-secret-token-value"
    assert site.tone_of_voice == "сухой"


def test_duplicate_domain_rejected(admin_client, site_payload):
    admin_client.post("/api/admin/sites", json=site_payload)
    resp = admin_client.post("/api/admin/sites", json=site_payload)
    assert resp.status_code == 400
    assert "уже" in resp.json()["detail"]


def test_manager_sees_site_list_without_tokens(admin_client, manager_client, site_payload):
    admin_client.post("/api/admin/sites", json=site_payload)
    resp = manager_client.get("/api/sites")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "Стройбаза Самара"
    assert "api_token" not in body[0]
    assert "api_token_enc" not in body[0]


def test_inactive_site_hidden_from_manager_list(admin_client, manager_client, site_payload):
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    admin_client.put(f"/api/admin/sites/{site_id}",
                     json={**site_payload, "api_token": "", "is_active": False})
    assert manager_client.get("/api/sites").json() == []


def patch_site_api(monkeypatch, parent_url="/poleznye-stati/", reference_html="<img><img>"):
    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": parent_url}
        return {"id": page_id, "text": reference_html}

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.list_section_pages",
        lambda self, prefix: [{"id": 1, "title": "A", "url": prefix + "a/"}])


def test_sync_fills_prefix_images_and_page_count(admin_client, site_payload, monkeypatch):
    patch_site_api(monkeypatch, reference_html="<p>t</p><img><img><img>")
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body == {"ok": True, "url_prefix": "/poleznye-stati/", "pages": 1,
                    "reference_images": 3, "detail": ""}


def test_sync_result_is_persisted_on_the_site(admin_client, db_session, site_payload,
                                              monkeypatch):
    from app.models.site import Site

    patch_site_api(monkeypatch)
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    admin_client.post(f"/api/admin/sites/{site_id}/sync")

    site = db_session.get(Site, site_id)
    assert site.articles_url_prefix == "/poleznye-stati/"
    assert site.reference_images == 2
    assert site.reference_synced_at is not None


def test_sync_reports_api_failure_without_raising(admin_client, site_payload, monkeypatch):
    from app.sites.client import SiteAPIError

    def boom(self, page_id):
        raise SiteAPIError("страница 25: HTTP 403: Forbidden")

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", boom)
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "403" in body["detail"]


def test_sync_reports_bad_reference_without_raising(admin_client, site_payload, monkeypatch):
    patch_site_api(monkeypatch, reference_html="<p>текст без картинок</p>")
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "ни одной картинки" in body["detail"]


def test_site_list_exposes_readiness(admin_client, manager_client, site_payload, monkeypatch):
    """Менеджеру важно одно: можно ли по этому сайту запускать партию."""
    patch_site_api(monkeypatch)
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    assert manager_client.get("/api/sites").json()[0]["is_ready"] is False

    admin_client.post(f"/api/admin/sites/{site_id}/sync")
    ready = manager_client.get("/api/sites").json()[0]
    assert ready["is_ready"] is True
    assert ready["reference_images"] == 2


def test_watermark_upload_stores_file(admin_client, site_payload, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.admin_sites.config.media_dir", str(tmp_path))
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    resp = admin_client.post(f"/api/admin/sites/{site_id}/watermark",
                             files={"file": ("mark.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert resp.status_code == 200
    stored = tmp_path / "watermarks" / f"{site_id}.png"
    assert stored.exists()


# --- ретраи и атомарность (замечания ревью Task 11) ---


def test_sync_retries_on_5xx_and_succeeds(admin_client, site_payload, monkeypatch):
    """5xx на сайте — временная штука (перегрузка, деплой). Первая попытка
    проваливается, вторая — та же самая — проходит."""
    from app.sites.client import SiteAPIError

    calls = {"list": 0}

    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": "/poleznye-stati/"}
        return {"id": page_id, "text": "<img><img>"}

    def list_section_pages(self, prefix):
        calls["list"] += 1
        if calls["list"] == 1:
            raise SiteAPIError("список страниц: HTTP 500: боль", status_code=500)
        return [{"id": 1, "title": "A", "url": prefix + "a/"}]

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages", list_section_pages)
    monkeypatch.setattr("app.api.admin_sites.time.sleep", lambda seconds: None)

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is True
    assert calls["list"] == 2


def test_sync_does_not_retry_on_404(admin_client, site_payload, monkeypatch):
    """404 — не тот id страницы. Повтор с тем же запросом даст тот же 404,
    поэтому счётчик вызовов обязан остаться на единице."""
    from app.sites.client import SiteAPIError

    calls = {"n": 0}

    def get_page(self, page_id):
        calls["n"] += 1
        raise SiteAPIError("страница 25: HTTP 404: Not Found", status_code=404)

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.time.sleep", lambda seconds: None)

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "404" in body["detail"]
    assert calls["n"] == 1


def test_sync_retries_on_network_error(admin_client, site_payload, monkeypatch):
    """status_code=None — сетевой сбой или сайт вернул не JSON: та же
    категория, что и 5xx, тоже повторяется."""
    from app.sites.client import SiteAPIError

    calls = {"n": 0}

    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": "/poleznye-stati/"}
        calls["n"] += 1
        if calls["n"] == 1:
            raise SiteAPIError("страница 312: сайт вернул не JSON: <html>...")
        return {"id": page_id, "text": "<img><img>"}

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages",
                        lambda self, prefix: [])
    monkeypatch.setattr("app.api.admin_sites.time.sleep", lambda seconds: None)

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is True
    assert calls["n"] == 2


def test_sync_gives_up_after_max_retries(admin_client, site_payload, monkeypatch):
    """Повторяющийся 5xx исчерпывает попытки и завершается отказом, а не
    бесконечным циклом."""
    from app.sites.client import SiteAPIError

    calls = {"n": 0}

    def list_section_pages(self, prefix):
        calls["n"] += 1
        raise SiteAPIError("список страниц: HTTP 503: боль", status_code=503)

    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.get_page",
        lambda self, page_id: {"id": page_id, "url": "/poleznye-stati/", "text": "<img>"})
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages", list_section_pages)
    monkeypatch.setattr("app.api.admin_sites.time.sleep", lambda seconds: None)

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert calls["n"] == 3


def test_sync_failure_leaves_site_fields_unchanged(admin_client, db_session, site_payload,
                                                    monkeypatch):
    """Синхронизация — одна транзакция: если обход раздела не удался (после
    того как эталон уже был бы готов записаться), в БД не должно появиться
    ни эталона, ни префикса — иначе отчёт "не получилось" врёт о состоянии
    сайта."""
    from app.models.site import Site
    from app.sites.client import SiteAPIError

    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.get_page",
        lambda self, page_id: {"id": page_id, "url": "/poleznye-stati/", "text": "<img><img>"})
    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.list_section_pages",
        lambda self, prefix: (_ for _ in ()).throw(
            SiteAPIError("список страниц: HTTP 404: Not Found", status_code=404)))

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]

    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False

    db_session.expire_all()
    site = db_session.get(Site, site_id)
    assert site.articles_url_prefix == ""
    assert site.reference_html == ""
    assert site.reference_images == 0
    assert site.reference_synced_at is None


def test_sync_failure_preserves_previous_successful_cache(admin_client, db_session,
                                                           site_payload, monkeypatch):
    """Кеш эталона от прошлой успешной синхронизации не должен затираться,
    если следующая синхронизация не удалась — иначе один сетевой сбой
    оставляет сайт вовсе без эталона, и статьи станет не по чему собирать."""
    from app.models.site import Site
    from app.sites.client import SiteAPIError

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]

    monkeypatch.setattr(
        "app.api.admin_sites.SiteClient.get_page",
        lambda self, page_id: {"id": page_id, "url": "/poleznye-stati/",
                               "text": "<p>t</p><img><img>"})
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages",
                        lambda self, prefix: [])
    first = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert first["ok"] is True

    db_session.expire_all()
    site = db_session.get(Site, site_id)
    old_prefix = site.articles_url_prefix
    old_images = site.reference_images
    old_html = site.reference_html
    old_synced_at = site.reference_synced_at
    assert old_images == 2

    def boom(self, prefix):
        raise SiteAPIError("список страниц: HTTP 404: Not Found", status_code=404)

    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages", boom)
    second = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert second["ok"] is False

    db_session.expire_all()
    site = db_session.get(Site, site_id)
    assert site.articles_url_prefix == old_prefix
    assert site.reference_images == old_images
    assert site.reference_html == old_html
    assert site.reference_synced_at == old_synced_at
```

- [x] **Step 6: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_sites.py -v`
Expected: FAIL — 404 на `/api/admin/sites`

- [x] **Step 7: Публичный список сайтов**

`execution/backend/app/api/sites.py`:

```python
"""Список сайтов для выпадающих списков. Токенов здесь нет ни в каком виде —
менеджеру они не нужны, а лишнее поле в ответе рано или поздно утечёт в лог."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.site import Site
from app.models.user import User

router = APIRouter(prefix="/api/sites", tags=["sites"])


class SiteBrief(BaseModel):
    id: int
    name: str
    domain: str
    publish_target: str
    url_prefix: str
    reference_images: int
    # Готовность считается на бэкенде: у менеджера нет доступа к карточке сайта,
    # и разбираться, чего именно не хватает, — не его задача.
    is_ready: bool


@router.get("", response_model=list[SiteBrief])
def list_sites(db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)) -> list[SiteBrief]:
    sites = db.scalars(select(Site).where(Site.is_active.is_(True)).order_by(Site.name)).all()
    return [
        SiteBrief(
            id=site.id, name=site.name, domain=site.domain,
            publish_target=site.publish_target,
            url_prefix=site.articles_url_prefix,
            reference_images=site.reference_images,
            is_ready=bool(site.articles_url_prefix and site.reference_html
                          and site.reference_images),
        )
        for site in sites
    ]
```

- [x] **Step 8: Админский роутер сайтов**

`execution/backend/app/api/admin_sites.py`:

```python
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.config import config
from app.models.site import Site
from app.models.user import User
from app.settings.crypto import SecretDecryptionError, decrypt, encrypt, mask
from app.sites.client import SiteAPIError, SiteClient
from app.sites.reference import ReferenceError, sync_site_reference

router = APIRouter(prefix="/api/admin/sites", tags=["admin-sites"])

# Три попытки с растущей паузой — то же решение, что и для RouterAI
# (TextClient._call, app/ai/text.py): повторяем 5xx и сетевые сбои, не тратим
# попытки на 4xx, где повтор с тем же запросом гарантированно даёт тот же
# результат (см. "Требование по ретраям" в плане Task 11).
SYNC_MAX_RETRIES = 3
SYNC_RETRY_BACKOFF = 0.5  # секунды; пауза перед следующей попыткой — backoff * 2**attempt


def _sync_is_retryable(exc: SiteAPIError) -> bool:
    """status_code is None — сетевой сбой или сайт вернул не JSON; >= 500 —
    отказ на стороне сайта. Оба класса могут исчезнуть сами при повторе.
    400/401/403/404/413 (неверный токен, нет родительской страницы, файл
    слишком велик, некорректные данные) повторять бессмысленно — та же
    граница, что и _NON_RETRYABLE для RouterAI в app/ai/text.py."""
    return exc.status_code is None or exc.status_code >= 500


class SiteIn(BaseModel):
    name: str
    domain: str
    base_url: str
    api_token: str = ""            # пусто при обновлении = «не менять»
    is_active: bool = True
    site_description: str = ""
    tone_of_voice: str = ""
    publish_target: str = "pages"
    articles_parent_id: int | None = None
    reference_article_id: int | None = None
    image_style_prompt: str = ""
    cover_mode: str = "prompt"
    cover_style_prompt: str = ""
    builder_template_html: str = ""
    builder_parent_id: int | None = None
    teaser_category_id: int | None = None
    teaser_city_id: int | None = None
    teaser_location_id: int | None = None


class SiteOut(SiteIn):
    id: int
    watermark_path: str = ""
    # Поля ниже заполняет синхронизация, руками они не редактируются — поэтому
    # их нет в SiteIn: пришедшее с фронта значение всё равно было бы затёрто.
    articles_url_prefix: str = ""
    reference_images: int = 0
    reference_synced_at: datetime | None = None


def _to_out(site: Site) -> SiteOut:
    try:
        token = decrypt(site.api_token_enc, config.encryption_key) if site.api_token_enc else ""
        shown = mask(token) if token else ""
    except SecretDecryptionError as exc:
        shown = f"ОШИБКА: {exc}"
    return SiteOut(
        id=site.id, name=site.name, domain=site.domain, base_url=site.base_url,
        api_token=shown, is_active=site.is_active,
        site_description=site.site_description, tone_of_voice=site.tone_of_voice,
        publish_target=site.publish_target, articles_parent_id=site.articles_parent_id,
        reference_article_id=site.reference_article_id,
        image_style_prompt=site.image_style_prompt,
        cover_mode=site.cover_mode, cover_style_prompt=site.cover_style_prompt,
        builder_template_html=site.builder_template_html,
        builder_parent_id=site.builder_parent_id,
        teaser_category_id=site.teaser_category_id,
        teaser_city_id=site.teaser_city_id,
        teaser_location_id=site.teaser_location_id,
        watermark_path=site.watermark_path,
        articles_url_prefix=site.articles_url_prefix,
        reference_images=site.reference_images,
        reference_synced_at=site.reference_synced_at,
    )


def _apply(site: Site, payload: SiteIn) -> None:
    for field, value in payload.model_dump(exclude={"api_token"}).items():
        setattr(site, field, value)
    if payload.api_token:
        site.api_token_enc = encrypt(payload.api_token, config.encryption_key)


def _get_or_404(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "сайт не найден")
    return site


def open_client(db: Session, site: Site) -> SiteClient:
    """Собирает клиент с расшифрованным токеном. Используется и задачами Celery."""
    return SiteClient(site.base_url, decrypt(site.api_token_enc, config.encryption_key))


@router.get("", response_model=list[SiteOut])
def list_sites(db: Session = Depends(get_db),
               _user: User = Depends(require_role("admin"))):
    return [_to_out(s) for s in db.scalars(select(Site).order_by(Site.name)).all()]


@router.post("", response_model=SiteOut)
def create_site(payload: SiteIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    # Site.domain нормализуется валидатором модели (Task 9) при записи, поэтому
    # в базе лежит уже lower/strip. Сравнивать нужно с тем же приведением —
    # иначе "Example.ru" при существующем "example.ru" проскочит эту проверку
    # и упадёт на самом commit() необработанным IntegrityError вместо
    # человеческого 400.
    domain = payload.domain.strip().lower()
    if db.scalars(select(Site).where(Site.domain == domain)).first():
        raise HTTPException(400, f"сайт {domain} уже заведён")
    if not payload.api_token:
        raise HTTPException(400, "токен обязателен при создании сайта")
    site = Site(api_token_enc="")
    _apply(site, payload)
    db.add(site)
    db.commit()
    return _to_out(site)


@router.put("/{site_id}", response_model=SiteOut)
def update_site(site_id: int, payload: SiteIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    site = _get_or_404(db, site_id)
    # Та же проверка, что и в create_site, и по той же причине: домен
    # нормализуется валидатором модели, поэтому сравнивать надо нормализованное
    # значение. Без проверки смена домена на уже занятый роняет уникальный
    # индекс необработанным IntegrityError — 500 вместо внятного 400.
    domain = (payload.domain or "").strip().lower()
    clash = db.scalars(
        select(Site).where(Site.domain == domain, Site.id != site.id)).first()
    if clash:
        raise HTTPException(400, f"сайт {domain} уже заведён")
    _apply(site, payload)
    db.commit()
    return _to_out(site)


@router.delete("/{site_id}")
def delete_site(site_id: int, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    db.delete(_get_or_404(db, site_id))
    db.commit()
    return {"ok": True}


class SyncResult(BaseModel):
    ok: bool
    url_prefix: str = ""
    pages: int = 0
    reference_images: int = 0
    detail: str = ""


@router.post("/{site_id}/sync", response_model=SyncResult)
def sync_site(site_id: int, db: Session = Depends(get_db),
              _user: User = Depends(require_role("admin"))):
    """Одна кнопка проверяет всё сразу: токен, раздел и эталон. Неверный токен
    или не тот id эталона должны обнаруживаться здесь, а не в середине партии
    из десяти статей.

    Ошибки возвращаются телом со `ok: false`, а не 4xx: это диагностика чужого
    сайта, а не отказ нашего API — фронту нужно показать текст, а не свалиться
    в общий обработчик ошибок.

    Синхронизация трогает два шага (эталон и список страниц раздела) и должна
    записаться в БД как одна операция: `sync_site_reference` вызывается с
    `commit=False`, коммит — один, в конце, только когда оба шага прошли.
    Иначе отказ на втором шаге оставлял бы эталон в БД уже обновлённым,
    а ответ говорил бы "не получилось" — вводя администратора в заблуждение
    о реальном состоянии сайта.

    5xx и сетевые сбои (`SiteAPIError.status_code` — `None` или `>= 500`)
    повторяются до `SYNC_MAX_RETRIES` раз; 4xx, `ReferenceError` (эталон без
    картинок, не задан id и т.п.) и `SecretDecryptionError` (неверный
    `ENCRYPTION_KEY`) — нет, повтор с тем же запросом даст тот же результат.
    """
    site = _get_or_404(db, site_id)
    for attempt in range(SYNC_MAX_RETRIES):
        try:
            client = open_client(db, site)
            sync_site_reference(db, site, client, commit=False)
            pages = client.list_section_pages(site.articles_url_prefix)
        except (ReferenceError, SecretDecryptionError) as exc:
            db.rollback()
            return SyncResult(ok=False, detail=str(exc))
        except SiteAPIError as exc:
            if not _sync_is_retryable(exc) or attempt == SYNC_MAX_RETRIES - 1:
                db.rollback()
                return SyncResult(ok=False, detail=str(exc))
            time.sleep(SYNC_RETRY_BACKOFF * (2**attempt))
            continue
        db.commit()
        return SyncResult(ok=True, url_prefix=site.articles_url_prefix, pages=len(pages),
                          reference_images=site.reference_images)


@router.post("/{site_id}/watermark")
def upload_watermark(site_id: int, file: UploadFile = File(...),
                     db: Session = Depends(get_db),
                     _user: User = Depends(require_role("admin"))):
    site = _get_or_404(db, site_id)
    directory = Path(config.media_dir) / "watermarks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{site_id}.png"
    path.write_bytes(file.file.read())
    site.watermark_path = str(path)
    db.commit()
    return {"ok": True, "watermark_path": site.watermark_path}
```

Ревью Task 11 нашло три реальных дефекта в первой версии кода (коммит
`fix: замечания ревью по синхронизации — атомарность, повторы, сохранность
кеша`):

- Абзац «Требование по ретраям» в начале Task 11 декларировал, что 5xx и
  сетевые сбои должны повторяться в `sync_site` — но литеральный код Step 8
  не делал ни одного повтора: любая ошибка сразу становилась `ok: false`.
  Декларация разошлась с реализацией. Добавлены `SYNC_MAX_RETRIES = 3`,
  `SYNC_RETRY_BACKOFF = 0.5` и `_sync_is_retryable` (граница — `None`/`>= 500`
  повторять, иначе нет), цикл повторов в `sync_site` зеркалит
  `TextClient._call` (app/ai/text.py, Task 7).
- `sync_site_reference` коммитила сама, до того как `sync_site` успевал
  обойти список страниц раздела: отказ на втором шаге (`list_section_pages`)
  оставлял эталон и префикс в БД уже обновлёнными, хотя ответ говорил
  `ok: false` — пользователь видел «не получилось», хотя часть работы уже
  состоялась. Добавлен параметр `commit: bool = True` (по образцу
  `SettingsService.set`/`set_secret`, app/settings/service.py); `sync_site`
  теперь вызывает `sync_site_reference(..., commit=False)` и коммитит один
  раз в конце, только когда оба шага прошли; на любом отказе — `db.rollback()`,
  чтобы уже изменённые в памяти поля `site` не разъехались с БД.
- Мутационная проверка (заменить `if images == 0: raise ReferenceError(...)`
  на вариант, который перед этим стирает `site.reference_html` и коммитит)
  показала, что ни один тест не проверял сохранность кеша эталона при отказе
  синхронизации — мутация выживала. Добавлен
  `test_sync_failure_does_not_clobber_previous_cache`
  (`tests/test_sites_reference.py`) и его API-аналоги
  `test_sync_failure_leaves_site_fields_unchanged`,
  `test_sync_failure_preserves_previous_successful_cache`
  (`tests/test_api_sites.py`) — плановый код и без изменений не трогает эти
  поля до успешной проверки, но это факт поведения теперь закреплён тестом,
  а не только чтением исходника.

- [x] **Step 9: Подключить роутеры**

В `execution/backend/app/main.py` заменить импорт и подключение на:

```python
from app.api import admin_settings, admin_sites, auth, sites

app.include_router(auth.router)
app.include_router(admin_settings.router)
app.include_router(sites.router)
app.include_router(admin_sites.router)
```

- [x] **Step 10: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_sites.py tests/test_sites_reference.py -v`
Expected: PASS — 30 passed (после ревью и правок; изначально — 23 passed)

- [x] **Step 11: Commit**

```bash
git add execution/backend/app/api execution/backend/app/sites/reference.py execution/backend/app/main.py execution/backend/tests/test_api_sites.py execution/backend/tests/test_sites_reference.py
git commit -m "feat: API сайтов, синхронизация раздела и эталонной статьи"
```

---

## Фаза 3 — Промпты

### Task 12: Шаблоны промптов и их разрешение

**Files:**
- Create: `execution/backend/app/models/prompt_template.py`
- Modify: `execution/backend/app/models/__init__.py` (зарегистрировать `PromptTemplate`)
- Create: `execution/backend/app/ai/prompts.py`
- Modify: `execution/backend/app/seed.py`
- Test: `execution/backend/tests/test_ai_prompts.py`

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_ai_prompts.py`:

```python
import pytest

from app.ai.prompts import (PROMPT_KEYS, PROMPT_VARIABLES, PromptError, check_template,
                            render_prompt, resolve_prompt)
from app.models.prompt_template import PromptTemplate
from app.seed import seed_prompts


def test_seed_creates_global_defaults(db_session):
    seed_prompts(db_session)
    keys = {row.key for row in db_session.query(PromptTemplate).all()}
    assert keys == set(PROMPT_KEYS)


def test_seed_is_idempotent(db_session):
    seed_prompts(db_session)
    db_session.query(PromptTemplate).filter_by(key="topics").update({"text": "мой промпт"})
    db_session.commit()
    seed_prompts(db_session)
    row = db_session.query(PromptTemplate).filter_by(key="topics", site_id=None).one()
    assert row.text == "мой промпт"


def test_resolve_falls_back_to_global(db_session):
    seed_prompts(db_session)
    text = resolve_prompt(db_session, "topics", site_id=42)
    assert "{{ count }}" in text or "count" in text


def test_site_override_wins(db_session):
    seed_prompts(db_session)
    db_session.add(PromptTemplate(key="topics", site_id=42, text="персональный промпт"))
    db_session.commit()
    assert resolve_prompt(db_session, "topics", site_id=42) == "персональный промпт"


def test_missing_key_raises(db_session):
    with pytest.raises(PromptError, match="не найден"):
        resolve_prompt(db_session, "topics", site_id=None)


def test_render_substitutes_variables():
    result = render_prompt("Придумай {{ count }} тем для {{ site_name }}.",
                           {"count": 5, "site_name": "Стройбаза"})
    assert result == "Придумай 5 тем для Стройбаза."


def test_render_loops_over_list():
    result = render_prompt("{% for t in existing_titles %}- {{ t }}\n{% endfor %}",
                           {"existing_titles": ["А", "Б"]})
    assert result == "- А\n- Б\n"


def test_render_blocks_dangerous_attribute_access():
    """SandboxedEnvironment: промпт редактируется через админку, и обращение
    к внутренностям объектов из шаблона должно падать, а не выполняться."""
    with pytest.raises(PromptError):
        render_prompt("{{ topic.__class__.__mro__ }}", {"topic": "тема"})


def test_render_reports_syntax_error_as_text():
    with pytest.raises(PromptError, match="синтаксис"):
        render_prompt("{% for x in %}", {})


def test_render_reports_unknown_variable_by_name():
    """Опечатка в имени переменной обязана падать, а не молча подставлять
    пустоту: урезанный промпт уходит в платный запрос, и обнаружить это можно
    только по качеству статей, много позже правки шаблона."""
    with pytest.raises(PromptError, match="conut"):
        render_prompt("Придумай {{ conut }} тем.", {"count": 5})


def test_render_allows_optional_variable_via_is_defined():
    """Обратная сторона StrictUndefined: для необязательной переменной
    правильная форма — `is defined`, а не голое `{% if x %}`."""
    out = render_prompt("{% if extra is defined %}{{ extra }}{% endif %}ок", {})
    assert out == "ок"


def test_default_prompts_render_with_real_contexts(db_session):
    """Каждый дефолтный промпт прогоняется с тем набором переменных, который
    реально передаётся в бою (см. app/tasks.py и app/articles/builder.py).
    Ловит расхождение между шаблоном и вызывающим кодом — именно оно тихо
    отключало тематику сайта в промпте тем."""
    seed_prompts(db_session)
    contexts = {
        "topics": {"count": 5, "site_name": "X", "site_description": "описание",
                   "tone_of_voice": "тон", "existing_titles": ["А", "Б"]},
        "article_body": {"topic": "тема", "site_name": "X", "site_description": "описание",
                         "tone_of_voice": "тон", "reference_html": "<p>x</p>",
                         "image_count": 2, "image_paths": ["/a.webp", "/b.webp"]},
        "cover": {"topic": "тема", "cover_style": "стиль"},
        "content_image": {"topic": "тема", "paragraph": "иллюстрация 1 из 2",
                          "image_style": "стиль"},
    }
    for key in PROMPT_KEYS:
        # PROMPT_VARIABLES — то, по чему check_template судит о шаблоне из
        # админки. Если он разойдётся с реальным контекстом, админка начнёт
        # отклонять правильные правки или пропускать опечатки, а падать это
        # будет в Celery. Сверяем здесь, где контекст выписан явно.
        assert set(contexts[key]) == PROMPT_VARIABLES[key], key
        template = resolve_prompt(db_session, key, None)
        rendered = render_prompt(template, contexts[key])
        assert rendered.strip()


def test_empty_site_override_falls_back_to_global(db_session):
    """Промпт сайта из одних пробелов — это «не задан», а не «задан пустым»."""
    seed_prompts(db_session)
    db_session.add(PromptTemplate(key="topics", site_id=1, text="   \n  "))
    db_session.commit()
    assert resolve_prompt(db_session, "topics", 1) == resolve_prompt(db_session, "topics", None)


def test_duplicate_key_for_same_site_is_rejected(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add(PromptTemplate(key="topics", site_id=1, text="a"))
    db_session.commit()
    db_session.add(PromptTemplate(key="topics", site_id=1, text="b"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_same_key_for_different_sites_is_allowed(db_session):
    db_session.add(PromptTemplate(key="topics", site_id=1, text="a"))
    db_session.add(PromptTemplate(key="topics", site_id=2, text="b"))
    db_session.commit()
    assert db_session.query(PromptTemplate).filter_by(key="topics").count() == 2


def test_duplicate_global_prompt_is_rejected(db_session):
    """UniqueConstraint(key, site_id) глобальные шаблоны не различает: в SQL
    NULL не равен сам себе, и две строки с site_id=NULL проходят (проверено на
    живом Postgres). Их разводит отдельный частичный индекс — иначе
    resolve_prompt брал бы первую попавшуюся из дублей."""
    from sqlalchemy.exc import IntegrityError

    db_session.add(PromptTemplate(key="topics", site_id=None, text="первый"))
    db_session.commit()
    db_session.add(PromptTemplate(key="topics", site_id=None, text="второй"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_default_prompts_pass_their_own_check(db_session):
    """Дефолтные шаблоны обязаны проходить ту же проверку, что и шаблоны из
    админки. Иначе первый же админ, открывший дефолт и нажавший «Сохранить»
    без единой правки, получит 400."""
    seed_prompts(db_session)
    for key in PROMPT_KEYS:
        check_template(resolve_prompt(db_session, key, None), key)


def test_check_template_names_the_misspelled_variable():
    """{{ site_desription }} синтаксически безупречен — на рендере с
    StrictUndefined это отказ, но ждать боевого прогона незачем."""
    with pytest.raises(PromptError) as exc:
        check_template("Пиши про {{ site_desription }}.", "topics")
    assert "site_desription" in str(exc.value)
    # Список доступных имён в тексте ошибки: без него админ видит «неизвестная
    # переменная» и идёт искать правильное написание в исходники.
    assert "site_description" in str(exc.value)


def test_check_template_allows_loop_variables():
    """Переменная цикла объявлена самим шаблоном и неизвестной не считается."""
    check_template("{% for title in existing_titles %}- {{ title }}\n{% endfor %}", "topics")


def test_check_template_without_key_checks_only_syntax():
    """Прогон на экране «Тест» ключа не знает: там переменные задаёт админ."""
    check_template("{{ что_угодно }}")
    with pytest.raises(PromptError, match="синтаксис"):
        check_template("{% for x in %}")


def test_prompt_variables_cover_every_key():
    assert set(PROMPT_VARIABLES) == set(PROMPT_KEYS)
```

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_prompts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.prompt_template'`

- [x] **Step 3: Модель**

`execution/backend/app/models/prompt_template.py`:

```python
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
```

- [x] **Step 4: Разрешение и рендер**

`execution/backend/app/ai/prompts.py`:

```python
"""Разрешение промпта (сайт → глобальный дефолт) и безопасный рендер Jinja2."""

from jinja2 import StrictUndefined, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompt_template import PromptTemplate

PROMPT_KEYS = ("topics", "article_body", "cover", "content_image")

# Набор переменных, который каждому промпту реально передаёт боевой код
# (app/tasks.py и app/articles/builder.py). Объявлен здесь, а не разбросан по
# местам вызова, ради двух проверок сразу: check_template отклоняет опечатку в
# имени переменной при сохранении шаблона в админке, а
# test_default_prompts_render_with_real_contexts сверяет этот список с
# контекстами, которые собирает вызывающий код. Без первой проверки опечатка
# вида {{ site_desription }} сохраняется с ответом 200 (синтаксис-то верный) и
# всплывает только на рендере в Celery-задаче — то есть посреди партии, после
# того как за предыдущие статьи уже заплачено.
PROMPT_VARIABLES: dict[str, frozenset[str]] = {
    "topics": frozenset({"count", "site_name", "site_description", "tone_of_voice",
                         "existing_titles"}),
    "article_body": frozenset({"topic", "site_name", "site_description", "tone_of_voice",
                               "reference_html", "image_count", "image_paths"}),
    "cover": frozenset({"topic", "cover_style"}),
    "content_image": frozenset({"topic", "paragraph", "image_style"}),
}

# undefined=StrictUndefined: с дефолтным Undefined опечатка в имени переменной
# ({{ conut }} вместо {{ count }}) молча превращается в пустую строку — шаблон
# рендерится «успешно», часть инструкции исчезает, и урезанный промпт уходит в
# платный запрос к модели. Обнаружить это можно только по качеству статей,
# то есть сильно позже и без связи с правкой промпта. Так уже было: шаблон
# topics обращался к site_description и tone_of_voice, а вызывающий код их не
# передавал — тематика сайта тихо не доезжала до модели.
#
# Обратная сторона: при StrictUndefined условие {% if x %} для необязательной
# переменной тоже падает. Правильная форма — {% if x is defined %}.
#
# В контекст рендера передаются только плоские значения (строки, числа, списки
# строк). ORM-объекты передавать нельзя: песочница ограничивает доступ к
# «небезопасным» атрибутам, но обычные атрибуты объекта из шаблона доступны.
_env = SandboxedEnvironment(autoescape=False, trim_blocks=False, lstrip_blocks=False,
                            undefined=StrictUndefined)


class PromptError(RuntimeError):
    pass


def resolve_prompt(db: Session, key: str, site_id: int | None) -> str:
    """Сначала переопределение сайта, затем глобальный дефолт."""
    if site_id is not None:
        override = db.scalars(
            select(PromptTemplate).where(PromptTemplate.key == key,
                                         PromptTemplate.site_id == site_id)).first()
        if override and override.text.strip():
            return override.text

    default = db.scalars(
        select(PromptTemplate).where(PromptTemplate.key == key,
                                     PromptTemplate.site_id.is_(None))).first()
    if default is None:
        raise PromptError(f"промпт {key!r} не найден — выполни seed_prompts")
    return default.text


def check_template(template_text: str, key: str | None = None) -> None:
    """Проверка шаблона без рендера — для сохранения в админке (Task 13), где
    значения переменных ещё неизвестны. Без неё сломанный шаблон ложится в БД
    с ответом 200 и взрывается позже, внутри Celery-задачи генерации статьи,
    где ошибку уже никто не свяжет с правкой промпта.

    При известном `key` проверяются и имена переменных: `{{ site_desription }}`
    синтаксически безупречен, но на рендере с StrictUndefined это отказ. Ждать
    его до боевого прогона незачем — набор переменных для каждого ключа
    известен заранее (PROMPT_VARIABLES), так что опечатка называется по имени
    прямо в форме редактирования.

    Переменные цикла (`{% for title in existing_titles %}`) в список
    неизвестных не попадают: find_undeclared_variables считает объявленным всё,
    что шаблон присваивает сам."""
    try:
        ast = _env.parse(template_text)
    except TemplateError as exc:
        raise PromptError(f"ошибка шаблона (синтаксис): {exc}") from exc

    allowed = PROMPT_VARIABLES.get(key or "")
    if allowed is None:
        return
    unknown = sorted(meta.find_undeclared_variables(ast) - allowed)
    if unknown:
        raise PromptError(
            f"неизвестные переменные: {', '.join(unknown)}. "
            f"Для промпта {key!r} доступны: {', '.join(sorted(allowed))}")


def render_prompt(template_text: str, variables: dict) -> str:
    try:
        return _env.from_string(template_text).render(**variables)
    except TemplateError as exc:
        # Промпты редактируются через админку, поэтому ошибка шаблона — обычная
        # пользовательская ошибка, а не сбой сервиса: её надо показать текстом.
        raise PromptError(f"ошибка шаблона (синтаксис или доступ): {exc}") from exc
```

- [x] **Step 5: Дефолтные промпты**

Добавь в конец `execution/backend/app/seed.py`:

```python
from app.ai.prompts import PROMPT_KEYS  # noqa: E402
from app.models.prompt_template import PromptTemplate  # noqa: E402

DEFAULT_PROMPTS = {
    # Тематику задаёт site_description, а не домен и не список прошлых статей:
    # по ним модель промахивается — у стройбазы и у производителя смесей рубрика
    # называется одинаково, а нужные темы разные.
    "topics": """Ты редактор блога сайта «{{ site_name }}».

О сайте и его аудитории:
{{ site_description }}

Тон материалов: {{ tone_of_voice }}

Предложи {{ count }} тем для полезных статей. Требования:
- тема попадает в тематику сайта и отвечает на реальный вопрос его аудитории;
- заголовок 40–80 символов, без кликбейта и без года в тексте;
- темы не должны повторять и близко пересекаться с уже опубликованными.

Уже опубликованные статьи:
{% for title in existing_titles %}- {{ title }}
{% endfor %}

Верни СТРОГО JSON-массив строк без пояснений, например:
["Как выбрать фундамент для дома на глинистой почве", "Чем утеплять каркасный дом"]""",

    "article_body": """Напиши полезную статью на тему «{{ topic }}» для сайта
«{{ site_name }}».

О сайте и его аудитории:
{{ site_description }}

Тон материала: {{ tone_of_voice }}

Требования к тексту:
- объём 2500–3000 символов;
- практическая польза, конкретика, без воды и рекламных обещаний;
- никаких упоминаний цен, акций и сроков — они устаревают;
- ровно {{ image_count }} иллюстраций внутри текста.

Требования к разметке: точно повтори структуру, набор тегов и CSS-классы
эталонной статьи этого сайта, приведённой ниже. Не добавляй своих классов,
не подключай стили и скрипты.

Иллюстрации вставь тегами <img> ровно с этими путями, по одному на смысловой блок:
{% for path in image_paths %}- {{ path }}
{% endfor %}

Эталонная статья (образец разметки, НЕ образец темы):
{{ reference_html }}

Верни СТРОГО JSON-объект:
{"title": "...", "html": "...", "meta_description": "...", "meta_keywords": "..."}""",

    "cover": """Составь промпт для генератора изображений — обложка статьи
«{{ topic }}».

Стиль обложки: {{ cover_style }}

Обложка — витрина статьи: общий план, узнаваемый сюжет, никакого текста и
надписей на изображении. Верни только сам промпт одной строкой, без пояснений.""",

    "content_image": """Составь промпт для генератора изображений — иллюстрация
к фрагменту статьи «{{ topic }}».

Фрагмент: {{ paragraph }}

Стиль: {{ image_style }}

Изображение поясняет смысл фрагмента. Без текста и надписей на изображении.
Верни только сам промпт одной строкой, без пояснений.""",
}


def _add_missing_prompts(db: Session) -> None:
    existing = {
        row.key for row in
        db.query(PromptTemplate).filter(PromptTemplate.site_id.is_(None)).all()
    }
    for key in PROMPT_KEYS:
        if key not in existing:
            db.add(PromptTemplate(key=key, site_id=None, text=DEFAULT_PROMPTS[key]))


def seed_prompts(db: Session) -> None:
    """Идемпотентна: отредактированный в админке промпт не перезаписывается."""
    _add_missing_prompts(db)
    try:
        db.commit()
    except IntegrityError:
        # Тот же конкурентный сценарий, что и в seed_settings выше: Task 13
        # зовёт seed_prompts на каждом GET /api/admin/prompts, поэтому два
        # админа, открывшие экран промптов на пустой БД, оба проходят
        # SELECT-фазу (видят пусто) раньше, чем кто-то из них коммитит.
        # Второй ловит конфликт частичного уникального индекса
        # uq_prompt_key_global (Task 12) — проверено на живом Postgres 16:
        # без этой ветки его GET отвечает 500. После rollback вставленное
        # конкурентом видно, поэтому повторный проход добавляет только
        # действительно недостающее.
        db.rollback()
        _add_missing_prompts(db)
        db.commit()
```

**Правка Task 13:** `seed_prompts`, `check_template` и `PROMPT_VARIABLES` выше
приведены уже с исправлениями, найденными при исполнении Task 13 (обработка
гонки на GET, проверка шаблона при сохранении). Сам Task 12 закоммичен без них.

- [x] **Step 6: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_prompts.py -v`
Expected: PASS — 21 passed (9 в самом Task 12, остальные добавлены в Task 13)

- [x] **Step 7: Миграция**

Добавь `PromptTemplate` в `app/models/__init__.py` (реестр моделей, см. Task 2,
Step 3) — `alembic/env.py` и `tests/conftest.py` подхватят её через `import
app.models` без собственных правок. Затем:

Run:
```bash
cd execution && docker compose run --rm backend alembic revision --autogenerate -m "prompt_templates"
docker compose run --rm backend alembic upgrade head
```
Expected: `Running upgrade <prev> -> <hash>, prompt_templates`

- [x] **Step 8: Commit**

```bash
git add execution/backend/app/models/prompt_template.py execution/backend/app/ai/prompts.py execution/backend/app/seed.py execution/backend/alembic execution/backend/tests/test_ai_prompts.py
git commit -m "feat: шаблоны промптов с переопределением по сайту"
```

---

### Task 13: API промптов и тестовый прогон

Экран «Промпты» с кнопкой «Тест» — единственный инструмент отладки качества
текстов, заменяющий сегодняшний диалог с агентом (см. §7 и риск 1 спеки).

**Files:**
- Create: `execution/backend/app/ai/factory.py`
- Create: `execution/backend/app/api/admin_prompts.py`
- Modify: `execution/backend/app/main.py`
- Modify: `execution/backend/app/ai/prompts.py` — добавлены `check_template`
  и `PROMPT_VARIABLES` (блок приведён в Task 12, Step 4)
- Modify: `execution/backend/app/seed.py` — `seed_prompts` переживает гонку
  (блок приведён в Task 12, Step 5)
- Test: `execution/backend/tests/test_api_admin_prompts.py`
- Test: `execution/backend/tests/test_ai_factory.py`

**Требования, добавленные при исполнении задачи.** Первые три подтверждены на
живом Postgres 16 (не рассуждением: разовый скрипт с двумя сессиями), все —
мутационными проверками:

1. **`seed_prompts` обязана переживать конкурентный первый GET.** Экран зовёт
   её на каждом чтении (как экран настроек — `seed_settings`), поэтому два
   админа на пустой БД проходят SELECT-фазу одновременно, и второй получает
   `duplicate key value violates unique constraint "uq_prompt_key_global"` —
   500 на ровном месте. Обработка та же, что в `seed_settings`: rollback,
   повторный проход по недостающим ключам, один коммит.
2. **`PUT` обязан проверять существование сайта.** Без проверки строка с
   висячим `site_id` на Postgres валит коммит
   (`violates foreign key constraint "prompt_templates_site_id_fkey"`) — 500
   вместо 404, а на SQLite (внешние ключи выключены) молча ложится в БД.
3. **`PUT` обязан переживать конкурентную первую запись того же ключа** —
   `IntegrityError`, rollback, повтор как UPDATE (тот же приём, что в
   `SettingsService._upsert`, Task 5).
4. **`PUT` обязан проверять синтаксис шаблона** (`check_template`). Иначе
   сломанный шаблон сохраняется с 200 и взрывается позже, внутри
   Celery-задачи генерации статьи, где ошибку уже никто не свяжет с правкой
   промпта. Экран «Тест» тут не защита: сохранить можно и не нажав его.
5. **`PUT` обязан отклонять пустой глобальный шаблон.** Для переопределения
   сайта пустой текст осмыслен (`resolve_prompt` возвращается к глобальному),
   для самого глобального — это пустой платный запрос в модель.
6. **Ошибки конфигурации RouterAI — 400, а не 500 и не 502.** Незаполненный
   `routerai_api_key` (`get_secret` отдаёт `""`, а openai-клиент бракует
   только `None` — проверено на openai 1.59.6) и секрет, зашифрованный другим
   `ENCRYPTION_KEY` (`SecretDecryptionError`), оба чинятся в админке, а не на
   стороне провайдера. Фабрика переводит их в один тип `AIConfigError`.
7. **Прогон делается одной попыткой.** `llm_max_retries = 3` держал бы
   синхронный HTTP-запрос до ≈366 с (120 с таймаута × 3 + паузы backoff, см.
   `app/ai/text.py`), всё это время занимая и поток, и сессию БД. Ретраи нужны
   фоновым задачам, где повтор некому нажать.
8. **Число попыток ограничено сверху и снизу.** `llm_max_retries` проверяется
   в админке только как «целое число»: `0` превратил бы `TextClient._call` в
   цикл без единой итерации, а завышенное значение выносит статью за
   `ARTICLE_TIME_BUDGET_SECONDS` (Task 18). Потолки разные: у картинок
   `TIMEOUT = 180`, и третья попытка — это 555 с вместо посчитанных в
   `app/ai/images.py` 365 с на пачку.
9. **Дефолты берутся из `DEFAULT_SETTINGS`, а не из литералов в фабрике**, и
   пустая строка (админ стёр поле в форме) считается отсутствием значения:
   openai-клиент принимает `base_url=""` молча и падает потом на запросе
   невнятным «Invalid URL».
10. **Пустой отрендеренный промпт не уходит в модель** — платный запрос ни о
    чём с заведомо мусорным ответом.
11. **`PUT` обязан проверять имена переменных, а не только синтаксис.**
    `{{ site_desription }}` — валидный Jinja, `check_template` по синтаксису
    его пропускает, а на рендере со `StrictUndefined` (Task 12) это отказ.
    Первый рендер случится в Celery-задаче, посреди партии, когда за
    предыдущие статьи уже заплачено. Набор переменных для каждого ключа
    известен заранее, поэтому объявлен в `PROMPT_VARIABLES` и проверяется на
    сохранении: `check_template(text, key)` называет опечатку по имени и
    перечисляет доступные имена. Переменные цикла
    (`{% for title in existing_titles %}`) неизвестными не считаются —
    `find_undeclared_variables` относит к объявленным всё, что шаблон
    присваивает сам. На экране «Тест» ключа нет и набор имён не ограничен:
    там переменные задаёт сам админ, иначе нельзя прогнать черновик.
    `PROMPT_VARIABLES` — единственное объявление этого набора: тот же список
    сверяется в `test_default_prompts_render_with_real_contexts` (Task 12) с
    контекстами, которые собирает боевой код, поэтому расхождение с Task 15/16
    падает тестом, а не тихо запрещает админу правильную правку.

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_api_admin_prompts.py`:

```python
from types import SimpleNamespace

import pytest

from app.ai.text import LLMError, TextResult


@pytest.fixture
def seeded(db_session):
    from app.seed import seed_prompts

    seed_prompts(db_session)


@pytest.fixture
def site(db_session):
    from app.models.site import Site

    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e")
    db_session.add(site)
    db_session.commit()
    return site


def _stub(monkeypatch, **client):
    """Подменяет фабрику клиента: тесты API не ходят в платную модель."""
    monkeypatch.setattr("app.api.admin_prompts.build_text_client",
                        lambda db, **kwargs: SimpleNamespace(**client))


def test_manager_cannot_read_prompts(manager_client, seeded):
    assert manager_client.get("/api/admin/prompts").status_code == 403


def test_admin_lists_global_prompts(admin_client, seeded):
    body = admin_client.get("/api/admin/prompts").json()
    keys = {item["key"] for item in body if item["site_id"] is None}
    assert keys == {"topics", "article_body", "cover", "content_image"}


def test_admin_saves_site_override(admin_client, seeded, db_session, site):
    from app.ai.prompts import resolve_prompt

    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": site.id, "text": "свой промпт"})
    assert resp.status_code == 200
    assert resolve_prompt(db_session, "topics", site.id) == "свой промпт"


def test_saving_twice_updates_the_same_row(admin_client, seeded, db_session, site):
    """Второй PUT обязан обновлять строку, а не добавлять вторую: пара
    (key, site_id) уникальна, и без поиска существующей строки этот запрос
    падал бы IntegrityError-ом с 500 вместо 200."""
    from app.ai.prompts import resolve_prompt

    admin_client.put("/api/admin/prompts",
                     json={"key": "topics", "site_id": site.id, "text": "первый"})
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": site.id, "text": "второй"})
    assert resp.status_code == 200
    assert resolve_prompt(db_session, "topics", site.id) == "второй"
    assert len(admin_client.get("/api/admin/prompts").json()) == 5


def test_saving_global_prompt_replaces_default(admin_client, seeded, db_session):
    from app.ai.prompts import resolve_prompt

    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "cover", "site_id": None, "text": "новый глобальный"})
    assert resp.status_code == 200
    assert resolve_prompt(db_session, "cover", None) == "новый глобальный"
    assert len(admin_client.get("/api/admin/prompts").json()) == 4


def test_unknown_key_rejected(admin_client, seeded):
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "выдуманный", "text": "привет"})
    assert resp.status_code == 400
    assert "выдуманный" in resp.json()["detail"]


def test_unknown_site_rejected(admin_client, seeded):
    """Без этой проверки строка с несуществующим site_id либо ложится в БД
    (SQLite, внешние ключи выключены), либо валит commit необработанным
    IntegrityError — 500 вместо внятного 404."""
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": 999, "text": "текст"})
    assert resp.status_code == 404


def test_broken_template_is_rejected_on_save(admin_client, seeded):
    """Сломанный шаблон, сохранённый без проверки, взрывается позже — внутри
    Celery-задачи генерации статьи, где ошибку уже никто не свяжет с правкой
    промпта. Кнопка «Тест» тут не защита: сохранить можно и не нажав её."""
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "text": "{% for x in %}"})
    assert resp.status_code == 400
    assert "синтаксис" in resp.json()["detail"]


def test_empty_global_prompt_is_rejected(admin_client, seeded, db_session):
    """resolve_prompt отдаёт глобальный шаблон как есть, без проверки на
    пустоту (пустой текст — «использовать глобальный» — осмыслен только для
    переопределения сайта). Пустой глобальный шаблон означал бы пустой
    платный запрос в модель."""
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": None, "text": "   "})
    assert resp.status_code == 400


def test_empty_site_override_falls_back_to_global(admin_client, seeded, db_session, site):
    from app.ai.prompts import resolve_prompt
    from app.seed import DEFAULT_PROMPTS

    admin_client.put("/api/admin/prompts",
                     json={"key": "topics", "site_id": site.id, "text": "свой"})
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": site.id, "text": ""})
    assert resp.status_code == 200
    assert resolve_prompt(db_session, "topics", site.id) == DEFAULT_PROMPTS["topics"]


def test_concurrent_first_save_recovers(admin_client, seeded, db_session, site, monkeypatch):
    """Два админа сохраняют переопределение одного и того же ключа: оба
    проходят SELECT (строки нет) раньше, чем кто-то из них коммитит. Тот же
    класс гонки, что чинили в Task 5 для SettingsService._upsert."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import IntegrityError

    real_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            db_session.rollback()
            db_session.execute(
                sql_text("INSERT INTO prompt_templates (key, site_id, text) "
                         "VALUES ('cover', :site_id, 'от конкурента')"),
                {"site_id": site.id})
            real_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "cover", "site_id": site.id, "text": "наш"})
    assert resp.status_code == 200
    assert calls["n"] == 2
    monkeypatch.undo()
    from app.ai.prompts import resolve_prompt
    assert resolve_prompt(db_session, "cover", site.id) == "наш"


def test_concurrent_first_seed_recovers(admin_client, db_session, monkeypatch):
    """GET сеет дефолты на каждом обращении (как и экран настроек), поэтому
    два админа на пустой БД сталкиваются на уникальном индексе
    uq_prompt_key_global. Проверено на живом Postgres 16: без обработки
    IntegrityError второй GET отвечает 500."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import IntegrityError

    real_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            db_session.rollback()
            db_session.execute(
                sql_text("INSERT INTO prompt_templates (key, site_id, text) "
                         "VALUES ('topics', NULL, 'от конкурента')"))
            real_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)
    resp = admin_client.get("/api/admin/prompts")
    assert resp.status_code == 200
    assert calls["n"] == 2
    keys = [item["key"] for item in resp.json()]
    assert sorted(keys) == ["article_body", "content_image", "cover", "topics"]


def test_test_endpoint_returns_rendered_prompt_and_answer(admin_client, seeded, monkeypatch):
    _stub(monkeypatch,
          complete_text=lambda prompt: TextResult("ответ модели", 10, 20, 0.3))

    resp = admin_client.post("/api/admin/prompts/test", json={
        "text": "Придумай {{ count }} тем для {{ site_name }}.",
        "variables": {"count": 3, "site_name": "Стройбаза"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["rendered"] == "Придумай 3 тем для Стройбаза."
    assert body["answer"] == "ответ модели"
    assert body["tokens_total"] == 30
    assert body["cost"] == 0.3


def test_test_endpoint_reports_template_error(admin_client, seeded):
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "{% for x in %}", "variables": {}})
    assert resp.status_code == 400
    assert "синтаксис" in resp.json()["detail"]


def test_test_endpoint_reports_unknown_variable(admin_client, seeded):
    """StrictUndefined (Task 12): опечатка в имени переменной обязана быть
    видна на экране «Тест», а не тихо вырезать кусок инструкции."""
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "тем: {{ conut }}", "variables": {"count": 3}})
    assert resp.status_code == 400
    assert "conut" in resp.json()["detail"]


def test_test_endpoint_requires_admin(manager_client, seeded):
    resp = manager_client.post("/api/admin/prompts/test",
                               json={"text": "привет", "variables": {}})
    assert resp.status_code == 403


def test_test_endpoint_does_not_call_model_on_empty_render(admin_client, seeded, monkeypatch):
    """Пустой отрендеренный промпт — это платный запрос ни о чём и заведомо
    мусорный ответ."""
    def boom(db, **kwargs):
        raise AssertionError("клиент не должен собираться для пустого промпта")

    monkeypatch.setattr("app.api.admin_prompts.build_text_client", boom)
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "{{ x }}", "variables": {"x": "  "}})
    assert resp.status_code == 400


def test_test_endpoint_reports_llm_failure(admin_client, seeded, monkeypatch):
    def failing(prompt):
        raise LLMError("RouterAI отклонил ключ API (401)")

    _stub(monkeypatch, complete_text=failing)
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "привет", "variables": {}})
    assert resp.status_code == 502
    assert "401" in resp.json()["detail"]


def test_test_endpoint_reports_missing_api_key(admin_client, seeded):
    """Ключ RouterAI не заполнен: админ обязан увидеть 400 с названием
    настройки, а не 502 после похода в RouterAI за 401."""
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "привет", "variables": {}})
    assert resp.status_code == 400
    assert "routerai_api_key" in resp.json()["detail"]


def test_test_endpoint_reports_wrong_encryption_key(admin_client, seeded, db_session):
    from cryptography.fernet import Fernet

    from app.config import config
    from app.settings.service import SettingsService

    SettingsService(db_session, config.encryption_key).set_secret("routerai_api_key", "sk-x")
    original = config.encryption_key
    config.encryption_key = Fernet.generate_key().decode()
    try:
        resp = admin_client.post("/api/admin/prompts/test",
                                 json={"text": "привет", "variables": {}})
    finally:
        config.encryption_key = original
    assert resp.status_code == 400
    assert "ENCRYPTION_KEY" in resp.json()["detail"]


def test_test_endpoint_bounds_waiting_time(admin_client, seeded, monkeypatch):
    """Синхронный эндпоинт с llm_max_retries=3 держал бы админа до ≈366 с
    (120 с таймаута × 3 попытки + паузы backoff, см. app/ai/text.py).
    Прогон делается одной попыткой: админ сидит перед экраном и нажмёт
    «Тест» ещё раз сам."""
    seen = {}

    def spy(db, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(complete_text=lambda prompt: TextResult("ок", 1, 1, 0.0))

    monkeypatch.setattr("app.api.admin_prompts.build_text_client", spy)
    admin_client.post("/api/admin/prompts/test", json={"text": "привет", "variables": {}})
    assert seen["max_retries"] == 1


def test_variables_default_is_not_shared_between_requests(admin_client, seeded, monkeypatch):
    """pydantic v2 копирует изменяемый дефолт на каждый экземпляр (проверено
    на pydantic 2.10.4), поэтому variables={} безопасен — тест фиксирует это
    как требование, а не как счастливую случайность."""
    from app.api.admin_prompts import PromptTestIn

    first = PromptTestIn(text="a")
    first.variables["x"] = 1
    assert PromptTestIn(text="b").variables == {}


def test_misspelled_variable_is_rejected_on_save(admin_client, seeded, db_session):
    """Опечатка в имени переменной проходит проверку синтаксиса, но на рендере
    падает — а рендер случится в Celery-задаче, посреди партии, после того как
    за предыдущие статьи уже заплачено."""
    from app.ai.prompts import resolve_prompt
    from app.seed import DEFAULT_PROMPTS

    resp = admin_client.put("/api/admin/prompts", json={
        "key": "topics", "site_id": None,
        "text": "Придумай {{ count }} тем про {{ site_desription }}.",
    })
    assert resp.status_code == 400
    assert "site_desription" in resp.json()["detail"]
    assert resolve_prompt(db_session, "topics", None) == DEFAULT_PROMPTS["topics"]


def test_valid_variables_are_accepted_on_save(admin_client, seeded, db_session):
    resp = admin_client.put("/api/admin/prompts", json={
        "key": "topics", "site_id": None,
        "text": "Придумай {{ count }} тем про {{ site_description }} в тоне {{ tone_of_voice }}.",
    })
    assert resp.status_code == 200


def test_test_endpoint_does_not_restrict_variable_names(admin_client, seeded, monkeypatch):
    """На «Тесте» переменные задаёт сам админ, ключа промпта там нет — набор
    имён не ограничен, иначе нельзя прогнать произвольный черновик."""
    _stub(monkeypatch, complete_text=lambda prompt: TextResult("ответ", 1, 1, 0.0))
    resp = admin_client.post("/api/admin/prompts/test", json={
        "text": "{{ произвольное_имя }}", "variables": {"произвольное_имя": "значение"}})
    assert resp.status_code == 200
    assert resp.json()["rendered"] == "значение"
```

`execution/backend/tests/test_ai_factory.py`:

```python
"""Фабрика клиентов RouterAI: дефолты, границы числа попыток и понятные
ошибки конфигурации вместо 401 от провайдера."""

import pytest

from app.ai.factory import (
    IMAGE_MAX_RETRIES,
    TEXT_MAX_RETRIES,
    AIConfigError,
    build_image_generator,
    build_text_client,
    image_params,
)
from app.config import config
from app.settings.service import SettingsService


@pytest.fixture
def service(db_session):
    from app.seed import seed_settings

    seed_settings(db_session)
    settings = SettingsService(db_session, config.encryption_key)
    settings.set_secret("routerai_api_key", "sk-test")
    return settings


def test_text_client_reads_settings(db_session, service):
    service.set("text_model", "anthropic/claude-opus-5")
    client = build_text_client(db_session)
    assert client.model == "anthropic/claude-opus-5"
    assert client.client.api_key == "sk-test"
    assert str(client.client.base_url).startswith("https://routerai.ru/api/v1")


def test_image_generator_reads_settings(db_session, service):
    service.set("image_model", "openai/gpt-image-3")
    generator = build_image_generator(db_session)
    assert generator.model == "openai/gpt-image-3"
    assert generator.api_key == "sk-test"
    assert generator.url == "https://routerai.ru/api/v1/images"


def test_missing_api_key_is_named_before_the_request(db_session):
    """openai-клиент принимает api_key="" молча (проверено: openai 1.59.6
    бракует только None) и уходит в запрос ради 401 — дорогой по времени
    способ узнать, что поле просто не заполнено."""
    from app.seed import seed_settings

    seed_settings(db_session)
    with pytest.raises(AIConfigError) as exc:
        build_text_client(db_session)
    assert "routerai_api_key" in str(exc.value)
    with pytest.raises(AIConfigError):
        build_image_generator(db_session)


def test_wrong_encryption_key_becomes_config_error(db_session, service):
    """SettingsService бросает SecretDecryptionError; вызывающим (API и
    Celery) удобнее один тип ошибки конфигурации с готовым текстом."""
    from cryptography.fernet import Fernet

    original = config.encryption_key
    config.encryption_key = Fernet.generate_key().decode()
    try:
        with pytest.raises(AIConfigError) as exc:
            build_text_client(db_session)
    finally:
        config.encryption_key = original
    assert "ENCRYPTION_KEY" in str(exc.value)


def test_retries_setting_is_applied(db_session, service):
    service.set("llm_max_retries", "2")
    assert build_text_client(db_session).max_retries == 2
    assert build_image_generator(db_session).max_retries == 2


def test_retries_are_bounded_by_time_budget(db_session, service):
    """llm_max_retries проверяется в админке только как «целое число».
    0 превратил бы _call в цикл без единой итерации (мгновенный «LLM
    недоступна после 0 попыток»), а завышенное значение выносит одну статью
    за ARTICLE_TIME_BUDGET_SECONDS = 900 с."""
    service.set("llm_max_retries", "99")
    assert build_text_client(db_session).max_retries == TEXT_MAX_RETRIES
    assert build_image_generator(db_session).max_retries == IMAGE_MAX_RETRIES

    service.set("llm_max_retries", "0")
    assert build_text_client(db_session).max_retries == 1
    assert build_image_generator(db_session).max_retries == 1


def test_image_retries_stay_within_documented_budget():
    """Худший случай пачки картинок посчитан в app/ai/images.py при двух
    попытках: 180 с × 2 + пауза 5 с = 365 с. Общий llm_max_retries = 3 дал бы
    555 с и вместе с текстовыми вызовами вышел бы за бюджет статьи."""
    from app.ai.images import TIMEOUT

    assert TIMEOUT * IMAGE_MAX_RETRIES + 5 * sum(range(1, IMAGE_MAX_RETRIES)) <= 365


def test_broken_retries_setting_falls_back_to_default(db_session, service):
    """Значение могли править прямо в БД, минуя валидацию админки."""
    service.set("llm_max_retries", "три")
    assert build_text_client(db_session).max_retries == TEXT_MAX_RETRIES


def test_explicit_max_retries_wins(db_session, service):
    assert build_text_client(db_session, max_retries=1).max_retries == 1


def test_empty_setting_falls_back_to_default(db_session, service):
    """Админ стёр поле в форме: openai-клиент принимает base_url="" молча и
    падает уже на запросе невнятным «Invalid URL»."""
    service.set("routerai_base_url", "")
    service.set("text_model", "")
    client = build_text_client(db_session)
    assert str(client.client.base_url).startswith("https://routerai.ru/api/v1")
    assert client.model == "anthropic/claude-sonnet-4-6"


def test_image_params_keys_match_builder(db_session, service):
    """Ключи потребляет ArticleBuilder (Task 16): size, quality, workers."""
    assert set(image_params(db_session)) == {"size", "quality", "workers"}
    assert image_params(db_session) == {"size": "1536x1024", "quality": "medium",
                                        "workers": 4}


def test_image_workers_bounded(db_session, service):
    """ThreadPoolExecutor(max_workers=0) — необработанный ValueError внутри
    Celery-задачи; границы берутся из INT_RANGES, а не из литералов здесь."""
    service.set("image_workers", "0")
    assert image_params(db_session)["workers"] == 1
    service.set("image_workers", "40")
    assert image_params(db_session)["workers"] == 8
```

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_prompts.py tests/test_ai_factory.py -v`
Expected: FAIL — `No module named 'app.ai.factory'`, дальше 404 на `/api/admin/prompts`

- [x] **Step 3: Фабрика клиентов**

`execution/backend/app/ai/factory.py`:

```python
"""Сборка клиентов RouterAI из настроек в БД. Одна точка — чтобы задачи Celery
и API-эндпоинты не читали настройки каждый по-своему."""

import logging

from sqlalchemy.orm import Session

from app.ai.images import ImageGenerator
from app.ai.text import TextClient, build_client
from app.config import config
from app.seed import DEFAULT_SETTINGS, INT_RANGES
from app.settings.crypto import SecretDecryptionError
from app.settings.service import SettingsService

logger = logging.getLogger(__name__)

# Верхняя граница числа попыток на один вызов. llm_max_retries приходит из
# админки, где проверяется только «целое число» (INT_KEYS в app/seed.py), а
# длительность вызова упирается в бюджет статьи (ARTICLE_TIME_BUDGET_SECONDS
# = 900 с, Task 18):
#   текст:    REQUEST_TIMEOUT_SECONDS=120 × 3 + паузы backoff(2, 4) = 366 с;
#   картинки: TIMEOUT=180 × 2 + пауза 5 с = 365 с на пачку (генерируются
#             параллельно, см. app/ai/images.py — там этот расчёт и записан).
# Отсюда разные потолки: третья попытка картинки — это 555 с вместо 365 и,
# вместе с текстовыми вызовами той же статьи, гарантированный выход за
# бюджет. Нижняя граница — 1: при 0 цикл в TextClient._call не делает ни
# одной итерации и сразу отдаёт «LLM недоступна после 0 попыток».
TEXT_MAX_RETRIES = 3
IMAGE_MAX_RETRIES = 2

# Пауза между попытками текстового вызова. Держим здесь, а не в TextClient:
# дефолт класса (0.0) удобен тестам, боевое значение — вызывающему.
TEXT_BACKOFF_SECONDS = 2.0


class AIConfigError(RuntimeError):
    """Настройки не позволяют собрать клиента: нет ключа или он зашифрован
    другим ENCRYPTION_KEY. Отдельно от LLMError намеренно — это ошибка
    конфигурации панели (чинится в админке, HTTP 400), а не отказ RouterAI
    (HTTP 502)."""


def _service(db: Session) -> SettingsService:
    return SettingsService(db, config.encryption_key)


def _setting(service: SettingsService, key: str) -> str:
    """Дефолт берётся из DEFAULT_SETTINGS, а не из литерала на месте вызова:
    иначе один и тот же дефолт живёт в двух местах и расходится при первой же
    смене модели. Пустая строка (админ стёр поле в форме) — тоже дефолт:
    openai-клиент принимает base_url="" молча (проверено на openai 1.59.6),
    а падает потом на запросе невнятным «Invalid URL»."""
    return service.get_str(key, DEFAULT_SETTINGS[key]) or DEFAULT_SETTINGS[key]


def _api_key(service: SettingsService) -> str:
    try:
        key = service.get_secret("routerai_api_key")
    except SecretDecryptionError as exc:
        raise AIConfigError(str(exc)) from exc
    if not key:
        # openai-клиент бракует только api_key=None (проверено на openai
        # 1.59.6), а с пустой строкой уходит в запрос ради 401 — дорогой по
        # времени способ узнать, что поле просто не заполнено.
        raise AIConfigError(
            "ключ RouterAI не задан — заполните routerai_api_key в настройках")
    return key


def _retries(service: SettingsService, limit: int, override: int | None = None) -> int:
    if override is not None:
        return max(1, min(limit, override))
    default = int(DEFAULT_SETTINGS["llm_max_retries"])
    try:
        raw = service.get_int("llm_max_retries", default)
    except ValueError:
        # Через админку не пройдёт (INT_KEYS), но значение могли править
        # прямо в БД. Необработанный ValueError здесь означал бы 500 на
        # ровном месте — и в API, и в Celery-задаче.
        logger.warning("llm_max_retries не число — используем %s", default)
        return default
    value = max(1, min(limit, raw))
    if value != raw:
        logger.warning("llm_max_retries=%s вне диапазона 1..%s — используем %s",
                       raw, limit, value)
    return value


def build_text_client(db: Session, max_retries: int | None = None) -> TextClient:
    """max_retries переопределяется вызывающим для интерактивных прогонов
    (экран «Промпты», Task 13): три попытки — это до 366 с ожидания в
    синхронном HTTP-запросе."""
    service = _service(db)
    client = build_client(_setting(service, "routerai_base_url"), _api_key(service))
    return TextClient(
        client,
        model=_setting(service, "text_model"),
        max_retries=_retries(service, TEXT_MAX_RETRIES, max_retries),
        backoff=TEXT_BACKOFF_SECONDS,
    )


def build_image_generator(db: Session) -> ImageGenerator:
    service = _service(db)
    return ImageGenerator(
        base_url=_setting(service, "routerai_base_url"),
        api_key=_api_key(service),
        model=_setting(service, "image_model"),
        max_retries=_retries(service, IMAGE_MAX_RETRIES),
    )


def image_params(db: Session) -> dict:
    """Ключи потребляет ArticleBuilder (Task 16): size, quality, workers."""
    service = _service(db)
    low, high = INT_RANGES["image_workers"]
    try:
        workers = service.get_int("image_workers", int(DEFAULT_SETTINGS["image_workers"]))
    except ValueError:
        workers = int(DEFAULT_SETTINGS["image_workers"])
    return {
        "size": _setting(service, "image_size"),
        "quality": _setting(service, "image_quality"),
        # Границы — те же, что проверяет админка (INT_RANGES), а не литералы
        # здесь: ThreadPoolExecutor(max_workers=0) валит Celery-задачу
        # необработанным ValueError, а значение могли править прямо в БД.
        "workers": max(low, min(high, workers)),
    }
```

- [x] **Step 4: Роутер промптов**

`execution/backend/app/api/admin_prompts.py`:

```python
"""Экран «Промпты»: глобальные шаблоны, переопределения по сайту и прогон
шаблона на живой модели без сохранения результата."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.factory import AIConfigError, build_text_client
from app.ai.prompts import PROMPT_KEYS, PromptError, check_template, render_prompt
from app.ai.text import LLMError
from app.api.deps import get_db, require_role
from app.models.prompt_template import PromptTemplate
from app.models.site import Site
from app.models.user import User
from app.seed import seed_prompts

router = APIRouter(prefix="/api/admin/prompts", tags=["admin-prompts"])

# Одна попытка вместо llm_max_retries: три попытки — это до 366 с ожидания
# (120 с таймаута × 3 + паузы backoff, см. app/ai/text.py) в синхронном
# HTTP-запросе, который всё это время держит и поток, и сессию БД. Ретраи
# нужны фоновым задачам, где повтор некому нажать; здесь админ сидит перед
# экраном и нажмёт «Тест» сам.
TEST_MAX_RETRIES = 1


class PromptOut(BaseModel):
    id: int
    key: str
    site_id: int | None
    text: str


class PromptIn(BaseModel):
    key: str
    site_id: int | None = None
    text: str


class PromptTestIn(BaseModel):
    text: str
    # default_factory, а не {}: pydantic v2 копирует изменяемый дефолт на
    # каждый экземпляр (проверено на pydantic 2.10.4), но полагаться на это
    # поведение библиотеки в общем на запрос объекте не хочется.
    variables: dict = Field(default_factory=dict)


class PromptTestOut(BaseModel):
    rendered: str
    answer: str
    tokens_total: int
    cost: float


def _find(db: Session, key: str, site_id: int | None) -> PromptTemplate | None:
    # `== None` тут не ошибка и не требует ветки с .is_(None): SQLAlchemy
    # рендерит сравнение с None как `IS NULL` (проверено печатью запроса на
    # sqlalchemy 2.x), поэтому один и тот же вызов находит и глобальный
    # шаблон, и переопределение сайта.
    return db.scalars(
        select(PromptTemplate).where(PromptTemplate.key == key,
                                     PromptTemplate.site_id == site_id)).first()


@router.get("", response_model=list[PromptOut])
def list_prompts(db: Session = Depends(get_db),
                 _user: User = Depends(require_role("admin"))):
    seed_prompts(db)
    rows = db.scalars(select(PromptTemplate).order_by(PromptTemplate.key,
                                                      PromptTemplate.site_id)).all()
    return [PromptOut(id=r.id, key=r.key, site_id=r.site_id, text=r.text) for r in rows]


@router.put("", response_model=PromptOut)
def save_prompt(payload: PromptIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    """Проверки здесь, а не «потом разберёмся»: у сломанного промпта следующая
    точка обнаружения — Celery-задача генерации статьи, где ошибку уже никто
    не свяжет с правкой шаблона. Экран «Тест» тут не защита: сохранить можно
    и не нажав его."""
    if payload.key not in PROMPT_KEYS:
        raise HTTPException(400, f"неизвестный ключ промпта: {payload.key}")
    if payload.site_id is not None and db.get(Site, payload.site_id) is None:
        # Иначе строка с висячим site_id либо ложится в БД (SQLite, внешние
        # ключи выключены), либо валит commit необработанным IntegrityError.
        raise HTTPException(404, "сайт не найден")
    if payload.site_id is None and not payload.text.strip():
        # Для переопределения сайта пустой текст осмыслен — resolve_prompt
        # возвращается к глобальному шаблону. Для самого глобального пустой
        # текст означает пустой платный запрос в модель.
        raise HTTPException(400, "глобальный промпт не может быть пустым")
    try:
        check_template(payload.text, payload.key)
    except PromptError as exc:
        raise HTTPException(400, str(exc)) from exc

    row = _find(db, payload.key, payload.site_id)
    if row is None:
        row = PromptTemplate(key=payload.key, site_id=payload.site_id)
        db.add(row)
    row.text = payload.text
    try:
        db.commit()
    except IntegrityError:
        # Конкурентная первая запись того же ключа: между нашим SELECT
        # (промах) и INSERT строку успел вставить другой админ — тот же класс
        # гонки, что чинили в Task 5 для SettingsService._upsert. К моменту
        # повтора строка уже есть, поэтому оставшийся путь — UPDATE. Если её
        # всё же нет, причина конфликта другая, и прятать её нельзя.
        db.rollback()
        row = _find(db, payload.key, payload.site_id)
        if row is None:
            raise
        row.text = payload.text
        db.commit()
    return PromptOut(id=row.id, key=row.key, site_id=row.site_id, text=row.text)


@router.post("/test", response_model=PromptTestOut)
def test_prompt(payload: PromptTestIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    """Прогон шаблона без сохранения результата: видно и отрендеренный промпт,
    и ответ модели, и цену вопроса."""
    try:
        rendered = render_prompt(payload.text, payload.variables)
    except PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not rendered.strip():
        # Пустой промпт — платный запрос ни о чём с заведомо мусорным ответом.
        raise HTTPException(400, "шаблон отрендерился в пустую строку")

    try:
        client = build_text_client(db, max_retries=TEST_MAX_RETRIES)
    except AIConfigError as exc:
        # Ошибка настроек панели, а не отказ провайдера: 400, чтобы админ
        # чинил её у себя, а не искал проблему на стороне RouterAI.
        raise HTTPException(400, str(exc)) from exc
    try:
        result = client.complete_text(rendered)
    except LLMError as exc:
        raise HTTPException(502, f"RouterAI: {exc}") from exc

    return PromptTestOut(
        rendered=rendered, answer=result.text,
        tokens_total=result.tokens_prompt + result.tokens_completion,
        cost=result.cost,
    )
```

- [x] **Step 5: Подключить роутер**

В `execution/backend/app/main.py`:

```python
from app.api import admin_prompts, admin_settings, admin_sites, auth, sites

app.include_router(auth.router)
app.include_router(admin_settings.router)
app.include_router(sites.router)
app.include_router(admin_sites.router)
app.include_router(admin_prompts.router)
```

- [x] **Step 6: Правки в app/ai/prompts.py и app/seed.py**

`check_template` с `PROMPT_VARIABLES` и обработка гонки в `seed_prompts` —
блоки приведены целиком в Task 12 (Step 4 и Step 5), там же причины. Там же
обновлён `tests/test_ai_prompts.py`: проверки `check_template` по ключу и
сверка `PROMPT_VARIABLES` с боевыми контекстами.

- [x] **Step 7: Запустить тесты, убедиться что проходят**

Run: `cd execution && docker compose run --rm --no-deps backend pytest -q`
Expected: PASS — 231 passed (189 до задачи + 42 новых)

- [x] **Step 8: Commit**

```bash
git add execution/backend/app/ai/factory.py execution/backend/app/ai/prompts.py execution/backend/app/api/admin_prompts.py execution/backend/app/seed.py execution/backend/app/main.py execution/backend/tests/test_api_admin_prompts.py execution/backend/tests/test_ai_factory.py
git commit -m "feat: API промптов с тестовым прогоном"
```

---

## Фаза 4 — Статьи

### Task 14: Модели статей и журнала задач

> **Дефекты, найденные при ревью (исправлены и здесь, и в коде — см. Step 3/4):**
> 1. `ArticleBatch.site_id` и `Article.site_id` в исходном черновике плана были
>    `NOT NULL` с `ondelete="CASCADE"`. Это противоречит уже написанному коду
>    Task 18: `_to_out` в `app/api/article_batches.py` достаёт сайт через
>    `db.get(Site, batch.site_id)` и подставляет `"—"`, если сайта нет —
>    ветка, которая при `CASCADE` никогда не выполнилась бы (партия исчезла
>    бы вместе с сайтом раньше, чем кто-то увидел бы `"—"`). Удаление сайта
>    (`delete_site`, Task 11, уже в проде) с `CASCADE` тихо стёрло бы всю
>    историю опубликованных статей — `remote_url`/`remote_page_id` это
>    единственная запись о том, что реально было выложено на сайте. Исправлено
>    на `nullable=True` + `ondelete="SET NULL"`, симметрично `JobRun.site_id`.
>    Проверено эмпирически на живом Postgres (миграция `e25842d72da3`
>    применена): `DELETE FROM sites` оставляет строки `article_batches` и
>    `articles` на месте с `site_id = NULL`, `remote_page_id`/`remote_url`
>    сохранены.
> 2. `ArticleImage.kind` — добавлен `CheckConstraint("kind IN ('cover',
>    'content')")`. `kind` used как literal в `app/articles/builder.py`
>    (Task 16), опечатка в нём привела бы не к явной ошибке, а к
>    необработанному `StopIteration` несколькими шагами дальше
>    (`_upload_content_images`: `next(i for i in article.images if i.kind ==
>    "content" ...)`). Проверено эмпирически на Postgres: `INSERT ... kind =
>    'banner'` падает с `CheckViolation` на самом INSERT.
> 3. `Article.slug` — добавлен частичный уникальный индекс `(site_id, slug)
>    WHERE slug != ''` (тот же приём, что и `uq_prompt_key_global` в
>    `prompt_template.py`, Task 12). Живая проверка перед публикацией
>    (`_guard_duplicate_url`, Task 16) спрашивает сам сайт и не защищает от
>    гонки внутри своей же партии, если список страниц сайта кэширован или
>    eventually-consistent. Частичность обязательна: черновики до сборки
>    хранят `slug=""` по умолчанию, и в одной партии их одновременно
>    несколько (`test_batch_articles_relationship`) — сквозной
>    `UniqueConstraint` запретил бы вторую тему в партии. Проверено
>    эмпирически на Postgres: дубль `(site_id, slug)` с непустым slug падает
>    с `duplicate key value violates unique constraint`, два черновика с
>    `slug=""` в одном сайте и одинаковый slug на разных сайтах — проходят.
> 4. `Article.batch_id`, `ArticleImage.article_id`, `LlmUsage.job_run_id` —
>    добавлен `index=True`. Обоснование по факту использования в уже
>    написанном коде дальше по плану, не «на всякий случай»: `batch.articles`
>    (фильтр по `batch_id`) выполняется на каждый показ партии (Task 18
>    `read_batch`/`_to_out`, Task 17 `run_batch_sync`, Task 16 тесты);
>    `article.images` (фильтр по `article_id`) — на каждую загрузку картинки
>    статьи (Task 16 `_upload_content_images`); `job.usage` (фильтр по
>    `job_run_id`) — на каждую строку журнала на `/api/jobs` (Task 18
>    `list_jobs`, N+1 по конструкции). Ни `Article.site_id`, ни
>    `ArticleBatch.site_id`, ни `JobRun.site_id` индекс не получили — по
>    всему плану (Tasks 15–18, 22–23) они используются только как аргумент
>    `db.get(Site, ...)` (PK-поиск сайта), ни разу как `WHERE` по таблице
>    статей/партий/джобов.
>
> Остальные найденные вопросы решены без изменения кода (см. комментарии в
> `app/models/job.py`): `JobRun.status="running"` по умолчанию — осознанно
> (запись создаётся уже из тела запущенной Celery-задачи, а не до постановки
> в очередь, так что «никогда не стартовавшего running» не бывает; риск
> «стартовал и не досчитал» из-за убитого процесса остаётся, но `started_at`
> уже достаточен, чтобы такие записи находить запросом — предмет будущего
> экрана журнала, не этой задачи). `LlmUsage.cost`/`ArticleImage.cost`
> остаются `float` (источник данных сам float), но при суммировании в Task 18
> (`sum(u.cost for u in job.usage)`) отображаемое значение нужно округлять
> (`round(x, 2)`) — иначе на экране может появиться `5.399999999999999`.
> Секретов в `params_json`/`log_text` не обнаружено: все вызовы `_start_job`
> в Task 17 передают только `batch_id`/`count`/`article_id`, а текст ошибок
> (`str(exc)`) — это `response.text[:300]` с чужого сайта или сообщение
> `LLMError`/`ImageError`, ни то ни другое не включает `api_token`/ключ
> RouterAI по коду в `app/sites/client.py`, `app/ai/text.py`, `app/ai/images.py`.

**Files:**
- Create: `execution/backend/app/models/article.py`
- Create: `execution/backend/app/models/job.py`
- Modify: `execution/backend/app/models/__init__.py` (зарегистрировать `ArticleBatch`, `Article`, `ArticleImage`, `JobRun`, `LlmUsage`)
- Test: `execution/backend/tests/test_models_article.py`

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_models_article.py`:

```python
from app.models.article import Article, ArticleBatch, ArticleImage
from app.models.job import JobRun, LlmUsage


def test_batch_starts_in_topics_pending(db_session):
    batch = ArticleBatch(site_id=1, requested_count=10, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    assert batch.status == "topics_pending"


def test_article_starts_as_draft(db_session):
    batch = ArticleBatch(site_id=1, requested_count=1, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, site_id=1, topic="Как выбрать фундамент")
    db_session.add(article)
    db_session.commit()
    assert article.status == "draft"
    assert article.remote_page_id is None


def test_batch_articles_relationship(db_session):
    batch = ArticleBatch(site_id=1, requested_count=2, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    db_session.add_all([
        Article(batch_id=batch.id, site_id=1, topic="Тема 1"),
        Article(batch_id=batch.id, site_id=1, topic="Тема 2"),
    ])
    db_session.commit()
    db_session.refresh(batch)
    assert [a.topic for a in batch.articles] == ["Тема 1", "Тема 2"]


def test_article_image_kinds(db_session):
    batch = ArticleBatch(site_id=1, requested_count=1, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, site_id=1, topic="Т")
    db_session.add(article)
    db_session.commit()
    db_session.add_all([
        ArticleImage(article_id=article.id, kind="cover", position=0, prompt="p"),
        ArticleImage(article_id=article.id, kind="content", position=1, prompt="p"),
    ])
    db_session.commit()
    db_session.refresh(article)
    assert {i.kind for i in article.images} == {"cover", "content"}


def test_job_run_records_who_and_what(db_session):
    job = JobRun(kind="generate_topics", site_id=1, created_by_id=1,
                 params_json={"count": 10})
    db_session.add(job)
    db_session.commit()
    assert job.status == "running"
    assert job.params_json["count"] == 10


def test_llm_usage_links_to_job(db_session):
    job = JobRun(kind="build_article", site_id=1, created_by_id=1, params_json={})
    db_session.add(job)
    db_session.commit()
    db_session.add(LlmUsage(job_run_id=job.id, kind="image", model="openai/gpt-image-2",
                            cost=5.4))
    db_session.commit()
    db_session.refresh(job)
    assert job.usage[0].cost == 5.4


# --- дополнительные тесты: структурные решения, найденные при ревью плана ---


def test_article_image_kind_rejects_unknown_value(db_session):
    """kind — по сути перечисление cover|content (используется как literal
    в app/articles/builder.py, Task 16). Опечатка в этой строке в будущем
    коде (например, при доработке Task 17) не должна тихо лечь в таблицу —
    из неё потом читают через `next(i for i in article.images if i.kind ==
    "content" ...)`, и непойманный из-за опечатки кадр уронит сборку статьи
    необработанным StopIteration вместо внятной ошибки. CHECK на уровне БД
    ловит это сразу при INSERT, а не через несколько шагов конвейера."""
    from sqlalchemy.exc import IntegrityError

    batch = ArticleBatch(site_id=1, requested_count=1, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, site_id=1, topic="Т")
    db_session.add(article)
    db_session.commit()
    db_session.add(ArticleImage(article_id=article.id, kind="banner", position=0))
    try:
        db_session.commit()
        assert False, "ожидался IntegrityError на недопустимом kind"
    except IntegrityError:
        db_session.rollback()


def test_duplicate_slug_within_site_is_rejected(db_session):
    """Два черновика с одинаковым slug на одном сайте целили бы в один и тот
    же url (builder.py: `articles_url_prefix + slug + '/'`). Живая проверка
    на сайте (_guard_duplicate_url, Task 16) защищает от этого только если
    список страниц сайта уже отдаёт свежесозданную страницу — при кэширующем
    или eventually-consistent списочном эндпоинте окно гонки есть. Частичный
    уникальный индекс — страховка в БД, а не замена проверки на сайте."""
    from sqlalchemy.exc import IntegrityError

    batch = ArticleBatch(site_id=1, requested_count=2, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    db_session.add(Article(batch_id=batch.id, site_id=1, topic="А", slug="chem-uteplit"))
    db_session.commit()
    db_session.add(Article(batch_id=batch.id, site_id=1, topic="Б", slug="chem-uteplit"))
    try:
        db_session.commit()
        assert False, "ожидался IntegrityError на дублирующемся slug в рамках сайта"
    except IntegrityError:
        db_session.rollback()


def test_empty_slug_does_not_collide_between_draft_articles(db_session):
    """Черновики до сборки (status=draft) имеют slug="" по умолчанию — их в
    партии может быть много одновременно (test_batch_articles_relationship).
    Частичный индекс должен игнорировать пустую строку, иначе второй черновик
    в партии не сохранился бы."""
    batch = ArticleBatch(site_id=1, requested_count=2, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    db_session.add_all([
        Article(batch_id=batch.id, site_id=1, topic="А"),
        Article(batch_id=batch.id, site_id=1, topic="Б"),
    ])
    db_session.commit()   # не должно бросить IntegrityError


def test_same_slug_allowed_on_different_sites(db_session):
    """Уникальность slug — в рамках сайта, а не глобальная: у двух разных
    сайтов разделы независимы, совпадение url между ними — не проблема."""
    batch1 = ArticleBatch(site_id=1, requested_count=1, created_by_id=1)
    batch2 = ArticleBatch(site_id=2, requested_count=1, created_by_id=1)
    db_session.add_all([batch1, batch2])
    db_session.commit()
    db_session.add_all([
        Article(batch_id=batch1.id, site_id=1, topic="А", slug="odna-tema"),
        Article(batch_id=batch2.id, site_id=2, topic="Б", slug="odna-tema"),
    ])
    db_session.commit()   # не должно бросить IntegrityError
```

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_article.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.article'`

Фактически: подтверждено, `ModuleNotFoundError: No module named 'app.models.article'`.

- [x] **Step 3: Модели статей**

`execution/backend/app/models/article.py`:

```python
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
```

- [x] **Step 4: Модели журнала**

`execution/backend/app/models/job.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.clock import utcnow
from app.db import Base

# JSONB на Postgres, обычный JSON на SQLite в тестах — один и тот же код моделей
# работает в обоих контурах.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class JobRun(Base):
    """Журнал фоновых задач: кто, что, когда и чем кончилось.

    status по умолчанию "running", а не "pending" — единственная модель в
    проекте с таким дефолтом. Это осознанно: JobRun создаётся уже внутри
    Celery-задачи, которая начала выполняться (см. _start_job в Task 17,
    app/tasks.py — вызывается из generate_topics_sync/run_batch_sync/
    retry_article_sync, то есть из тела уже запущенной задачи, а не перед
    постановкой в очередь). Если брокер недоступен, `.delay()`/`apply_async()`
    в API (Task 18) бросит исключение ДО того, как строка JobRun вообще
    появится, — то есть зависшего "running", который никогда не стартовал,
    таким путём не возникает.
    Остаточный риск — не «никогда не стартовавшая» запись, а «стартовавшая и
    не досчитавшая до конца»: воркер убит по OOM или SIGKILL, потеряно
    соединение с БД внутри `except` до commit — в этих случаях JobRun
    останется в "running" навсегда, потому что `_finish_job` не будет вызван.
    Обработчик SoftTimeLimitExceeded в tasks.py закрывает мягкий случай
    (истечение времени), но не жёсткий сбой процесса. Схема уже даёт всё
    нужное для обнаружения зависших записей без изменений: `started_at`
    есть у каждой JobRun, и запрос вида `status='running' AND started_at <
    now() - interval` находит их без дополнительного поля. Это не чинится
    в Task 14 — отмечено как риск для будущего экрана журнала задач
    (Task 18/23): такой запрос там нужно предусмотреть, а не изобретать поле.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))       # generate_topics | build_article
    # SET NULL: журнал расходов и логов должен пережить удаление сайта —
    # это операционная история/costs, ценность которой не привязана к тому,
    # заведён ли ещё сам сайт в панели. См. тот же выбор и то же обоснование
    # у ArticleBatch.site_id и Article.site_id (app/models/article.py).
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    params_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    celery_task_id: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|failed
    log_text: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    usage: Mapped[list["LlmUsage"]] = relationship(
        back_populates="job", cascade="all, delete-orphan")


class LlmUsage(Base):
    """Расход RouterAI. Картинка в качестве high стоит ≈16.8 единицы —
    расход надо видеть до того, как он станет сюрпризом.

    cost — float, не Decimal: источник данных сам float (TextResult.cost,
    ImageResult.cost в app/ai/text.py и app/ai/images.py приходят из ответа
    RouterAI как float), так что Decimal здесь дал бы ложную точность без
    исправления источника. Но Task 18 суммирует cost по всем LlmUsage джобы
    (`sum(u.cost for u in job.usage)`) — накопленная ошибка двоичного float
    на сумме из нескольких чисел может дать в ответе API что-то вроде
    5.399999999999999 вместо 5.4. Округление обязано делаться на стороне
    отображения (round(x, 2) в Task 18/25 при формировании ответа), а не
    здесь: хранить нужно то, что реально пришло от провайдера.
    """

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    # index=True: Task 18 (app/api/jobs.py) считает cost и tokens_total через
    # `job.usage` для каждой строки списка джобов на /api/jobs — то есть этот
    # фильтр по job_run_id выполняется на каждый показанный ряд журнала,
    # а не один раз на всю страницу.
    job_run_id: Mapped[int] = mapped_column(
        ForeignKey("job_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))       # text | image
    model: Mapped[str] = mapped_column(String(100), default="")
    tokens_prompt: Mapped[int] = mapped_column(Integer, default=0)
    tokens_completion: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["JobRun"] = relationship(back_populates="usage")
```

- [x] **Step 5: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_article.py -v`
Expected: PASS — 6 passed

Фактически: 10 passed (6 из плана + 4 дополнительных теста на CHECK-constraint
и частичный уникальный индекс, добавленных при ревью, см. врезку выше).

- [x] **Step 6: Миграция**

Добавь `ArticleBatch`, `Article`, `ArticleImage`, `JobRun` и `LlmUsage` в
`app/models/__init__.py` (реестр моделей, см. Task 2, Step 3) — `alembic/env.py`
и `tests/conftest.py` подхватят их через `import app.models` без собственных
правок. Затем:

Run:
```bash
cd execution && docker compose run --rm backend alembic revision --autogenerate -m "articles and jobs"
docker compose run --rm backend alembic upgrade head
```
Expected: `Running upgrade <prev> -> <hash>, articles and jobs`

Фактически: `Running upgrade 8fa16f835bee -> e25842d72da3, articles and jobs`.
Автогенерация корректно увидела `CheckConstraint`, частичный `Index` и все
`index=True` из Step 3/4 — сверено содержимым файла миграции построчно.

Эмпирическая проверка на живом Postgres (`docker compose up -d postgres`,
миграция применена, проверялось прямыми SQL-запросами через `psql`, не через
тесты — SQLite-тесты FK не проверяют, а частичный индекс и CHECK — сквозные
для обоих движков и уже покрыты тестами выше):
- `INSERT INTO article_images (..., kind) VALUES (..., 'banner')` →
  `ERROR: new row for relation "article_images" violates check constraint
  "ck_article_image_kind"`.
- Второй `INSERT INTO articles` с тем же `(site_id, slug)` → `ERROR: duplicate
  key value violates unique constraint "uq_article_site_slug"`; два черновика
  с `slug=''` на одном сайте и одинаковый slug на разных сайтах — проходят.
- `DELETE FROM sites WHERE id = 1` при существующих `article_batches`/
  `articles` с этим `site_id` → обе строки остаются, `site_id` становится
  `NULL`, `status`/`remote_page_id`/`remote_url` не тронуты.

- [x] **Step 7: Commit**

```bash
git add execution/backend/app/models execution/backend/alembic execution/backend/tests/test_models_article.py orchestration/2026-08-04-plan1-core-and-articles.md
git commit -m "feat: модели статей, партий и журнала задач"
```

---

### Task 15: Генерация тем и дедуп

**Files:**
- Create: `execution/backend/app/articles/__init__.py`
- Create: `execution/backend/app/articles/topics.py`
- Test: `execution/backend/tests/test_articles_topics.py`

- [x] **Step 1: Написать падающий тест**

`execution/backend/tests/test_articles_topics.py`:

```python
from app.articles.topics import filter_duplicates, normalize


def test_normalize_lowercases_and_drops_punctuation():
    # Без "Как" в начале: это слово — стоп-слово (см. _STOPWORDS), его отсев
    # проверяется отдельно в test_normalize_drops_stopwords. Здесь проверяем
    # только регистр и пунктуацию, а не пересечение с фильтром стоп-слов.
    assert normalize("Выбрать Фундамент!") == "vybrat fundament"


def test_normalize_drops_stopwords():
    assert normalize("Как и чем утеплить дом") == "uteplit dom"


def test_exact_duplicate_is_filtered():
    kept, dropped = filter_duplicates(["Чем утеплить дом"], ["Чем утеплить дом"])
    assert kept == []
    assert dropped == ["Чем утеплить дом"]


def test_case_and_punctuation_insensitive():
    kept, _ = filter_duplicates(["ЧЕМ УТЕПЛИТЬ ДОМ?"], ["Чем утеплить дом"])
    assert kept == []


def test_near_duplicate_by_keyword_overlap_is_filtered():
    """«Чем утеплить каркасный дом» и «Как утеплить каркасный дом зимой» —
    одна и та же статья с точки зрения читателя."""
    kept, _ = filter_duplicates(["Как утеплить каркасный дом зимой"],
                                ["Чем утеплить каркасный дом"])
    assert kept == []


def test_different_topic_is_kept():
    kept, dropped = filter_duplicates(["Как выбрать кровельное покрытие"],
                                      ["Чем утеплить каркасный дом"])
    assert kept == ["Как выбрать кровельное покрытие"]
    assert dropped == []


def test_duplicates_inside_proposed_list_are_filtered():
    kept, _ = filter_duplicates(["Чем утеплить дом", "Чем утеплить дом фасад"], [])
    assert len(kept) == 1


def test_empty_existing_keeps_everything():
    # Не "Тема A"/"Тема B": "A" транслитерируется в "a", а это же "a" — форма
    # русского союза "а" в _STOPWORDS. normalize("Тема A") даёт токены {tema}
    # (без "a"), normalize("Тема B") — {tema, b}; overlap 1/1 = 1.0 >= порога,
    # и функция (ошибочно для намерения этого теста) считает их дублями.
    # "Раз"/"Два" не пересекаются со стоп-словами и с собой.
    kept, dropped = filter_duplicates(["Тема Раз", "Тема Два"], [])
    assert len(kept) == 2
    assert dropped == []


def test_overlap_exactly_at_threshold_is_filtered():
    """OVERLAP_THRESHOLD = 0.6 — граница включительная (>=, не >). Пять
    значимых слов, три общих: overlap = 3 / 5 = 0.6 ровно."""
    kept, dropped = filter_duplicates(
        ["alpha bravo charlie delta echo"], ["alpha bravo charlie foxtrot golf"])
    assert kept == []
    assert dropped == ["alpha bravo charlie delta echo"]


def test_topic_made_only_of_stopwords_is_dropped_not_kept():
    """Мусорный ответ модели (одни стоп-слова/пунктуация) нормализуется в
    пустой набор токенов и намеренно уходит в dropped вместе с настоящими
    дублями — отдельной категории "невалидная тема" нет (см. комментарий у
    _is_duplicate). Тема без ключевых слов не должна попасть в kept."""
    kept, dropped = filter_duplicates(["Как и почему", "???"], [])
    assert kept == []
    assert dropped == ["Как и почему", "???"]
```

**Дефекты плана, найденные при прогоне (ревью Task 15):**

1. `test_normalize_lowercases_and_drops_punctuation` в исходном виде плана
   ожидал `normalize("Как выбрать Фундамент!") == "kak vybrat fundament"`, но
   "kak" — стоп-слово из `_STOPWORDS`, и тот же модуль в
   `test_normalize_drops_stopwords` ожидает, что "Как" из "Как и чем
   утеплить дом" будет отброшено. Два теста плана противоречили друг другу
   при одной и той же реализации: оба одновременно пройти не могут. Прогон
   подтвердил: `AssertionError: 'vybrat fundament' != 'kak vybrat fundament'`.
   Правка: слово "Как" убрано из примера теста (он и так не про стоп-слова).
2. `test_empty_existing_keeps_everything` в исходном виде плана использовал
   `["Тема A", "Тема B"]`. "A" транслитерируется в "a" — это та же форма,
   что и русский союз "а" в `_STOPWORDS`. В результате `normalize("Тема A")`
   даёт `{tema}` (без "a"), а `normalize("Тема B")` — `{tema, b}`; overlap
   1/1 = 1.0 ≥ 0.6, и функция (не по замыслу теста) считает их дублями.
   Прогон подтвердил: `assert 1 == 2` (осталась только "Тема A"). Правка:
   заменено на `["Тема Раз", "Тема Два"]`, не пересекающиеся со стоп-словами.

- [x] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_articles_topics.py -v`
Фактически: FAIL — `ModuleNotFoundError: No module named 'app.articles'` (совпало
с ожиданием плана). После исправления модуля два теста из Step 1 всё ещё падали
по причине дефектов самого теста (см. выше) — исправлены до перехода к Step 4.

- [x] **Step 3: Реализация**

`execution/backend/app/articles/__init__.py` — пустой файл.

`execution/backend/app/articles/topics.py`:

```python
"""Отбор тем: нормализация заголовков и отсев дублей.

Модель уже получает список существующих заголовков в промпте, но полагаться
только на неё нельзя — при десятках статей на сайте она начинает повторяться.
Локальный фильтр даёт детерминированную гарантию.
"""

from __future__ import annotations

from app.sites.client import slugify

# Порог пересечения значимых слов, при котором темы считаются одинаковыми.
OVERLAP_THRESHOLD = 0.6

_STOPWORDS = {
    "kak", "chem", "chto", "gde", "kogda", "pochemu", "zachem", "kakoy", "kakaya",
    "kakie", "i", "v", "na", "s", "so", "dlya", "iz", "po", "pri", "ili", "a", "no",
    "li", "ne", "svoimi", "rukami", "zimoy", "letom", "vesnoy", "osenyu",
}


def normalize(title: str) -> str:
    """Латиница, без пунктуации, без стоп-слов — форма для сравнения тем.

    Побочный эффект slugify, полезный именно здесь: мягкий/твёрдый знак
    транслитерируются в пустую строку (см. _TRANSLIT в app/sites/client.py),
    поэтому "ель" и "эль" после normalize совпадут, если бы вдруг встретились
    — для сравнения смысла тем это не создаёт наблюдаемых ложных дублей на
    реальных строительных заголовках (см. проверку в Task 15 плана), но
    зафиксировано как заимствованное поведение чужой функции, а не решение,
    принятое здесь намеренно.
    """
    words = slugify(title, limit=500).split("-")
    return " ".join(w for w in words if w and w not in _STOPWORDS)


def _tokens(title: str) -> set[str]:
    return set(normalize(title).split())


def _is_duplicate(candidate: set[str], known: list[set[str]]) -> bool:
    # Тема, состоящая только из пунктуации/стоп-слов (мусорный ответ модели,
    # например "???" или "Как и почему"), нормализуется в пустой набор токенов.
    # Осознанно считаем её "дублем", а не заводим отдельную категорию: с точки
    # зрения админа такая тема одинаково непригодна к публикации — неважно,
    # что именно написано в логе джобы (Task 17), она всё равно не станет
    # статьёй. Заводить третью корзину ради счётчика в текстовом логе
    # внутренней панели на несколько человек — избыточная точность.
    if not candidate:
        return True
    for other in known:
        if not other:
            continue
        # Нормировка по МЕНЬШЕМУ множеству, а не по объединению (не индекс
        # Жаккара) — намеренно консервативный отсев: короткая тема, целиком
        # содержащаяся в длинной ("утеплить дом" в "как правильно утеплить
        # дом из бруса своими силами"), считается дублем со стороны короткой,
        # даже если охват статей на деле разный. Проверено на реальных парах
        # (см. Task 15 плана): "утеплить дом" / "утеплить дом из бруса" —
        # действительно близкие темы для одного сайта, отсев оправдан; но
        # "утеплить дом" против "утеплить дом из кирпича" тоже схлопнутся,
        # хотя это разные материалы и разные статьи. Известное ограничение
        # формулы, порог не переоткрывался в рамках этой задачи.
        overlap = len(candidate & other) / min(len(candidate), len(other))
        if overlap >= OVERLAP_THRESHOLD:
            return True
    return False


def filter_duplicates(proposed: list[str],
                      existing_titles: list[str]) -> tuple[list[str], list[str]]:
    """Возвращает (принятые, отсеянные). Дубли ищутся и среди существующих
    статей сайта, и внутри самого предложенного списка.

    Известное ограничение: нет стемминга/лемматизации русских словоформ.
    "Как выбрать утеплитель" и "Выбор утеплителей" не пересекутся по токенам
    (fundament/fundamenta и т.п. — разные строки после транслитерации), хотя
    это одна тема в разных падеже/числе. Near-duplicate по словоформам мимо
    этого фильтра пройдёт молча — полноценная лемматизация вне рамок задачи.
    """
    known = [_tokens(title) for title in existing_titles]
    kept: list[str] = []
    dropped: list[str] = []

    for title in proposed:
        candidate = _tokens(title)
        if _is_duplicate(candidate, known):
            dropped.append(title)
            continue
        kept.append(title)
        known.append(candidate)

    return kept, dropped
```

Отличие от исходного текста плана: убран неиспользуемый `import re` (в модуле
он не применяется — вся работа с регулярками делает `slugify` внутри
`app/sites/client.py`), и добавлены комментарии, фиксирующие находки ревью
(см. ниже). Сигнатура `filter_duplicates(proposed, existing_titles) ->
(kept, dropped)` не менялась — она зафиксирована использованием в Task 17.

**Находки ревью — задокументированные ограничения (эмпирически проверено,
не рассуждением; исправление формулы/порога/стоп-слов не входило в рамки
задачи и не менялось без согласования):**

1. **`_is_duplicate`: пустой набор токенов → `dropped`, без отдельной
   категории «невалидная тема».** Мусорная тема от модели (например,
   `"Как и почему"` или `"???"`) окажется в `dropped` наравне с настоящими
   дублями, и лог Task 17 (`"отсеяно дублей: N"`) не различит эти два
   случая. Решение: смешение приемлемо для внутренней панели на несколько
   человек — `dropped` по смыслу означает «эта тема не станет статьёй»,
   а не строго «совпала с существующей». Заведение третьей корзины ради
   точности счётчика в текстовом логе избыточно. Покрыто тестом
   `test_topic_made_only_of_stopwords_is_dropped_not_kept`.
2. **Формула `overlap = |candidate ∩ other| / min(|candidate|, |other|)`
   (не индекс Жаккара, нормировка по меньшему множеству) — консервативный
   отсев с конкретным риском.** Проверено на реальных строительных парах:
   - `"утеплить дом"` vs `"как правильно утеплить дом из бруса своими
     силами"` → overlap = 2/2 = **1.0** (полный дубль по формуле, хотя
     охват статей разный — общая статья vs про брус конкретно);
   - `"утеплить дом из бруса"` vs `"утеплить дом из кирпича"` (РАЗНЫЕ
     материалы, разные статьи) → overlap = 2/3 = **0.667 ≥ 0.6** — тоже
     признаются дублями;
   - `"выбор фундамента для дома"` vs `"выбор фундамента для бани"`
     (разные объекты) → overlap = 2/3 = **0.667** — тоже дубль;
   - `"утепление стен пеноплексом"` vs `"утепление стен минватой"` (разные
     материалы) → overlap = 2/3 = **0.667** — тоже дубль.
   Риск реален: при обсуждении конкретных технологий (утепление бруса,
   кирпича, каркасника) короткое общее подмножество слов схлопывает разные
   по смыслу темы в одну. Порог/формулу не меняли — это поведенческая
   настройка, требующая согласования с человеком, а не структурная находка;
   зафиксировано как известное ограничение с конкретными числами.
3. **Короткие предлоги вне `_STOPWORDS` (`"под"`, `"над"`, `"через"`,
   `"без"`) — проверено, реальных ложных срабатываний не найдено.**
   На парах `"что положить под ламинат"` vs `"что находится под крышей"`
   (overlap 0.33) и `"что постелить под линолеум"` vs `"какой утеплитель
   под сайдинг"` (overlap 0.33) общий предлог "под" не поднимает overlap
   выше порога — в титулах из 3+ значимых слов один общий предлог не
   решает исход. Список стоп-слов заведомо неполон, но конкретного примера
   с реальным изменением исхода сравнения не найдено; не чинили.
4. **Отсутствие стемминга/лемматизации — подтверждено дважды, с разным
   знаком ошибки.** `"фундамент для дома"` vs `"фундаменты для дома"`
   (ед./мн. число) → overlap = 1/2 = **0.5 < 0.6** — near-duplicate
   пропущен (ложноотрицательный случай: похожие темы не считаются дублями).
   `"как выбрать утеплитель"` vs `"выбор утеплителей"` (та же тема, разные
   словоформы и часть речи) → overlap = **0.0** — дубль пропущен
   полностью. Оба подтверждают: без лемматизации словоформы, а не только
   пунктуация/регистр, ломают сравнение. Задокументировано в докстринге
   `filter_duplicates`; полноценная лемматизация — вне рамок этой задачи.
5. **`ъ`/`ь` → `""` в транслитерации (заимствовано из `slugify`, написанной
   для URL, а не для сравнения смысла).** Проверено: `normalize("ель") ==
   normalize("эль") == "el"` — теоретическая коллизия существует. На
   реальных строительных заголовках наблюдаемых ложных дублей не найдено:
   `normalize("подъезд") == "podezd"`, `normalize("подъем") == "podem"` —
   разные слова, поскольку ъ/ь у них не единственное различие. Оставлено
   как задокументированное заимствованное поведение (см. докстринг
   `normalize`), не как дефект.

- [x] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_articles_topics.py -v`
Фактически: PASS — 10 passed (8 из исходного плана + 2 добавленных ревью:
`test_overlap_exactly_at_threshold_is_filtered` — граничное значение порога
0.6 включительно, и `test_topic_made_only_of_stopwords_is_dropped_not_kept` —
поведение при пустом после нормализации наборе токенов). Полный набор:
`cd execution && docker compose run --rm --no-deps backend pytest -q` →
251 passed (241 предыдущих + 10 новых).

- [x] **Step 5: Commit**

```bash
git add execution/backend/app/articles execution/backend/tests/test_articles_topics.py
git commit -m "feat: отсев дублей тем статей"
```

---

### Task 16: Сборка одной статьи

Ядро процесса: текст → картинки → водяной знак → загрузка → страница-черновик → обложка.

> **Требование (найдено при ревью Task 8, эмпирически):** `_generate_content_images`
> генерирует контентные картинки параллельно через `ThreadPoolExecutor.map`.
> Если одна из параллельных генераций падает, `list(pool.map(render, ...))`
> бросает исключение при первом же неудачном результате в порядке подачи
> задач — а уже посчитанные к этому моменту результаты соседних, успешных
> генераций отбрасываются молча: цикл `for position, prompt, data, cost in
> sorted(rendered): ...`, который пишет `ArticleImage` и `LlmUsage`, вообще
> не выполняется, потому что до него не доходит. При этом сами HTTP-запросы
> к RouterAI за эти успешные картинки уже выполнились и, вероятно, уже
> оплачены — поток внутри `ThreadPoolExecutor` не отменяется истечением
> `with`-блока, он просто доработает и потом будет отброшен. Итог: расход
> денег на успешно сгенерированные, но выброшенные из-за соседнего отказа
> картинки не попадает в финансовый учёт партии (`LlmUsage`), хотя провайдер
> его уже учёл. Реализация Task 16 обязана записывать `ArticleImage`/
> `LlmUsage` по каждой успешно завершившейся генерации по мере её готовности
> (например, через `concurrent.futures.as_completed` вместо `pool.map`, с
> записью в БД сразу по получении результата), а не только после того, как
> весь список результатов собран целиком без исключений.

**Files:**
- Create: `execution/backend/app/articles/builder.py`
- Test: `execution/backend/tests/test_articles_builder.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_articles_builder.py`:

```python
import json
from types import SimpleNamespace

import pytest

from app.ai.images import ImageResult
from app.ai.text import JsonResult, TextResult
from app.articles.builder import ArticleBuilder, image_filename, image_paths_for


class FakeTextClient:
    def __init__(self, body: dict):
        self.body = body
        self.prompts = []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return JsonResult(self.body, 100, 200, 0.4)

    def complete_text(self, prompt):
        self.prompts.append(prompt)
        return TextResult("промпт картинки", 10, 20, 0.05)


class FakeImageGenerator:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, size, quality, crop):
        self.calls.append(prompt)
        return ImageResult(data=b"webp-bytes", size=(1600, 1066), cost=5.4, seconds=60)


class FakeSiteClient:
    def __init__(self):
        self.uploaded = []
        self.created = None
        self.cover = None
        self.fetched_pages = []

    def get_page(self, page_id):
        self.fetched_pages.append(page_id)
        return {"id": page_id, "text": "<article class='post'><p>эталон</p></article>"}

    def list_section_pages(self, prefix):
        return [{"id": 1, "title": "Старая статья", "url": prefix + "staraya/"}]

    def upload_file(self, data, filename, upload_to):
        self.uploaded.append(filename)
        return f"/media/{upload_to}{filename}"

    def create_page(self, title, url, html, parent_id, meta_description, meta_keywords):
        self.created = dict(title=title, url=url, html=html, parent_id=parent_id)
        return {"id": 501, "url": url}

    def set_page_cover(self, page_id, image_bytes, filename):
        self.cover = (page_id, filename)
        return "/media/staticpages/images/" + filename


@pytest.fixture
def prepared(db_session, admin):
    from app.models.article import Article, ArticleBatch
    from app.models.site import Site
    from app.seed import seed_prompts

    seed_prompts(db_session)
    site = Site(name="Стройбаза", domain="x.ru", base_url="https://x.ru",
                api_token_enc="e", articles_parent_id=25,
                articles_url_prefix="/poleznye-stati/",
                site_description="Стройбаза в Самаре", tone_of_voice="практичный",
                reference_article_id=312,
                reference_html="<article class='post'><p>эталон</p><img><img></article>",
                reference_images=2,
                image_style_prompt="фото стройки", cover_style_prompt="обложка")
    db_session.add(site)
    db_session.commit()
    batch = ArticleBatch(site_id=site.id, requested_count=1,
                         created_by_id=admin.id, status="running")
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, site_id=site.id, topic="Чем утеплить дом")
    db_session.add(article)
    db_session.commit()
    return SimpleNamespace(site=site, batch=batch, article=article)


def make_builder(db_session, prepared, site_client=None, body=None):
    body = body or {
        "title": "Чем утеплить каркасный дом",
        "html": "<article class='post'><p>Текст</p>"
                "<img src='/media/uploads/article-img/article_1-1.webp'>"
                "<img src='/media/uploads/article-img/article_1-2.webp'></article>",
        "meta_description": "описание",
        "meta_keywords": "утепление",
    }
    return ArticleBuilder(
        db=db_session,
        article=prepared.article,
        site=prepared.site,
        text_client=FakeTextClient(body),
        image_generator=FakeImageGenerator(),
        site_client=site_client or FakeSiteClient(),
        image_params={"size": "1536x1024", "quality": "medium", "workers": 2},
        watermark_bytes=b"",
        job_run_id=None,
    )


def test_image_filename_is_deterministic():
    assert image_filename(7, 0) == "article_7-cover.webp"
    assert image_filename(7, 2) == "article_7-2.webp"


def test_image_paths_use_article_img_dir():
    assert image_paths_for(7, 2) == [
        "/media/uploads/article-img/article_7-1.webp",
        "/media/uploads/article-img/article_7-2.webp",
    ]


def test_build_sets_title_slug_and_html(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()
    assert prepared.article.title == "Чем утеплить каркасный дом"
    assert prepared.article.slug == "chem-uteplit-karkasnyy-dom"
    assert "<article" in prepared.article.body_html


def test_build_publishes_draft_and_records_remote_id(db_session, prepared):
    site_client = FakeSiteClient()
    make_builder(db_session, prepared, site_client).build()
    assert prepared.article.status == "published"
    assert prepared.article.remote_page_id == 501
    assert site_client.created["url"] == "/poleznye-stati/chem-uteplit-karkasnyy-dom/"
    assert site_client.created["parent_id"] == 25


def test_build_uploads_content_images_and_cover(db_session, prepared):
    site_client = FakeSiteClient()
    make_builder(db_session, prepared, site_client).build()
    # 2 контентные картинки идут через filemanager, обложка — отдельным PATCH
    assert site_client.uploaded == ["article_1-1.webp", "article_1-2.webp"]
    assert site_client.cover == (501, "article_1-cover.webp")


def test_reference_comes_from_cache_without_network(db_session, prepared):
    """Эталон лежит в карточке сайта — за ним на сайт не ходим."""
    site_client = FakeSiteClient()
    builder = make_builder(db_session, prepared, site_client)
    builder.build()
    assert "эталон" in builder.text_client.prompts[0]
    assert site_client.fetched_pages == []


def test_image_count_follows_reference(db_session, prepared):
    """Сколько <img> в эталоне, столько картинок и генерируется — отдельной
    настройки «сколько картинок» нет."""
    prepared.site.reference_images = 3
    db_session.commit()
    site_client = FakeSiteClient()
    builder = make_builder(db_session, prepared, site_client)
    builder.build()
    assert site_client.uploaded == ["article_1-1.webp", "article_1-2.webp",
                                    "article_1-3.webp"]
    assert "3 иллюстраций" in builder.text_client.prompts[0]


def test_unsynced_site_fails_early(db_session, prepared):
    """Без синхронизации эталона генерировать нечего — падаем до трат на картинки."""
    prepared.site.reference_html = ""
    prepared.site.reference_images = 0
    db_session.commit()
    builder = make_builder(db_session, prepared)
    builder.build()
    assert prepared.article.status == "failed"
    assert "синхронизирован" in prepared.article.error_text
    assert builder.image_generator.calls == []


def test_site_profile_reaches_the_prompt(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()
    body_prompt = builder.text_client.prompts[0]
    assert "Стройбаза в Самаре" in body_prompt
    assert "практичный" in body_prompt


def test_slug_is_truncated_to_limit(db_session, prepared):
    long_body = {
        "title": "Очень длинный заголовок " * 10,
        "html": "<p>x</p>", "meta_description": "", "meta_keywords": "",
    }
    builder = make_builder(db_session, prepared, body=long_body)
    builder.build()
    assert len(prepared.article.slug) <= 70


def test_duplicate_url_is_skipped_not_recreated(db_session, prepared):
    class ExistingUrlClient(FakeSiteClient):
        def list_section_pages(self, prefix):
            return [{"id": 9, "title": "Старая",
                     "url": prefix + "chem-uteplit-karkasnyy-dom/"}]

    site_client = ExistingUrlClient()
    make_builder(db_session, prepared, site_client).build()
    assert site_client.created is None
    assert prepared.article.status == "failed"
    assert "уже есть" in prepared.article.error_text


def test_failure_is_recorded_on_article(db_session, prepared):
    class BrokenClient(FakeSiteClient):
        def create_page(self, **kwargs):
            from app.sites.client import SiteAPIError

            raise SiteAPIError("создание страницы: HTTP 403: Forbidden")

    make_builder(db_session, prepared, BrokenClient()).build()
    assert prepared.article.status == "failed"
    assert "403" in prepared.article.error_text


def test_model_returning_non_json_marks_article_failed(db_session, prepared):
    builder = make_builder(db_session, prepared)

    def broken_json(prompt):
        from app.ai.text import LLMError

        raise LLMError("модель вернула не JSON: извините")

    builder.text_client.complete_json = broken_json
    builder.build()
    assert prepared.article.status == "failed"
    assert "не JSON" in prepared.article.error_text
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.articles.builder'`

- [ ] **Step 3: Реализация**

`execution/backend/app/articles/builder.py`:

```python
"""Сборка одной статьи: текст → картинки → загрузка → страница-черновик → обложка.

Разбит на шаги-методы, чтобы падение на любом из них попадало в error_text
статьи, а не роняло всю партию.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.ai.factory import build_image_generator, build_text_client, image_params
from app.ai.images import ImageError
from app.ai.prompts import PromptError, render_prompt, resolve_prompt
from app.ai.text import LLMError
from app.ai.watermark import apply_watermark
from app.models.article import Article, ArticleImage
from app.models.job import LlmUsage
from app.models.site import Site
from app.sites.client import (
    ARTICLE_IMG_DIR,
    SLUG_LIMIT_ARTICLES,
    SLUG_LIMIT_PAGES,
    SiteAPIError,
    slugify,
)

COVER_CROP = "3:2"
CONTENT_CROP = "3:2"


def image_filename(article_id: int, position: int) -> str:
    """position=0 — обложка, дальше контентные по порядку."""
    suffix = "cover" if position == 0 else str(position)
    return f"article_{article_id}-{suffix}.webp"


def image_paths_for(article_id: int, count: int) -> list[str]:
    return [f"/media/{ARTICLE_IMG_DIR}{image_filename(article_id, i)}"
            for i in range(1, count + 1)]


class ArticleBuilder:
    def __init__(self, db: Session, article: Article, site: Site, text_client,
                 image_generator, site_client, image_params: dict,
                 watermark_bytes: bytes, job_run_id: int | None):
        self.db = db
        self.article = article
        self.site = site
        self.text_client = text_client
        self.image_generator = image_generator
        self.site_client = site_client
        self.image_params = image_params
        self.watermark_bytes = watermark_bytes
        self.job_run_id = job_run_id

    # --- публичный вход ---

    def build(self) -> None:
        self._set_status("generating")
        try:
            self._require_synced_reference()
            body = self._generate_body()
            self._apply_body(body)
            self._guard_duplicate_url()
            content_images = self._generate_content_images()
            self._upload_content_images(content_images)
            page = self._create_page()
            self._attach_cover(page["id"])
        except (LLMError, ImageError, SiteAPIError, PromptError) as exc:
            self.article.status = "failed"
            self.article.error_text = str(exc)
            self.db.commit()
            return
        self.article.status = "published"
        self.article.error_text = ""
        self.db.commit()

    # --- шаги ---

    def _set_status(self, status: str) -> None:
        self.article.status = status
        self.db.commit()

    def _slug_limit(self) -> int:
        return (SLUG_LIMIT_ARTICLES if self.site.publish_target == "articles"
                else SLUG_LIMIT_PAGES)

    def _require_synced_reference(self) -> None:
        """Проверка идёт до первого платного вызова: без эталона разметку взять
        неоткуда, и падать на этом после генерации картинок было бы обидно."""
        if not (self.site.reference_html and self.site.reference_images
                and self.site.articles_url_prefix):
            raise SiteAPIError(
                "эталон сайта не синхронизирован — нажми «Проверить и синхронизировать» "
                "на карточке сайта")

    def _image_count(self) -> int:
        """Сколько <img> в эталоне, столько картинок и генерируем."""
        return self.site.reference_images

    def _generate_body(self) -> dict:
        count = self._image_count()
        template = resolve_prompt(self.db, "article_body", self.site.id)
        prompt = render_prompt(template, {
            "topic": self.article.topic,
            "site_name": self.site.name,
            "site_description": self.site.site_description,
            "tone_of_voice": self.site.tone_of_voice,
            # Эталон берётся из кеша карточки — к сайту за ним не ходим.
            "reference_html": self.site.reference_html,
            "image_count": count,
            "image_paths": image_paths_for(self.article.id, count),
        })
        result = self.text_client.complete_json(prompt)
        self._record_usage("text", result.tokens_prompt, result.tokens_completion, result.cost)
        if not isinstance(result.data, dict) or "html" not in result.data:
            raise LLMError("модель вернула объект без поля html")
        return result.data

    def _apply_body(self, body: dict) -> None:
        self.article.title = body.get("title") or self.article.topic
        self.article.slug = slugify(self.article.title, limit=self._slug_limit())
        self.article.body_html = body["html"]
        self.article.meta_description = body.get("meta_description", "")
        self.article.meta_keywords = body.get("meta_keywords", "")
        self.db.commit()

    def _guard_duplicate_url(self) -> None:
        """Дубль url означает повторный прогон той же темы — молча создавать
        вторую страницу нельзя."""
        target = f"{self.site.articles_url_prefix}{self.article.slug}/"
        taken = {p.get("url") for p in self.site_client.list_section_pages(
            self.site.articles_url_prefix)}
        if target in taken:
            raise SiteAPIError(f"страница {target} уже есть на сайте")

    def _image_prompt(self, key: str, variables: dict) -> str:
        rendered = render_prompt(resolve_prompt(self.db, key, self.site.id), variables)
        result = self.text_client.complete_text(rendered)
        self._record_usage("text", result.tokens_prompt, result.tokens_completion, result.cost)
        return result.text.strip()

    def _generate_content_images(self) -> list[tuple[int, bytes]]:
        count = self._image_count()
        prompts = [
            self._image_prompt("content_image", {
                "topic": self.article.topic,
                "paragraph": f"иллюстрация {position} из {count}",
                "image_style": self.site.image_style_prompt,
            })
            for position in range(1, count + 1)
        ]

        def render(position_prompt):
            position, prompt = position_prompt
            result = self.image_generator.generate(
                prompt=prompt, size=self.image_params["size"],
                quality=self.image_params["quality"], crop=CONTENT_CROP)
            # Водяной знак — только на контентные картинки; обложка остаётся чистой.
            return position, prompt, apply_watermark(result.data, self.watermark_bytes), result.cost

        # Генерация идёт 40–140 секунд на кадр, поэтому параллельно.
        with ThreadPoolExecutor(max_workers=self.image_params["workers"]) as pool:
            rendered = list(pool.map(render, enumerate(prompts, start=1)))

        images = []
        for position, prompt, data, cost in sorted(rendered):
            self.db.add(ArticleImage(article_id=self.article.id, kind="content",
                                     position=position, prompt=prompt, cost=cost))
            self._record_usage("image", 0, 0, cost)
            images.append((position, data))
        self.db.commit()
        return images

    def _upload_content_images(self, images: list[tuple[int, bytes]]) -> None:
        for position, data in images:
            filename = image_filename(self.article.id, position)
            path = self.site_client.upload_file(data, filename, ARTICLE_IMG_DIR)
            image = next(i for i in self.article.images
                         if i.kind == "content" and i.position == position)
            image.remote_path = path
        self.db.commit()

    def _create_page(self) -> dict:
        page = self.site_client.create_page(
            title=self.article.title,
            url=f"{self.site.articles_url_prefix}{self.article.slug}/",
            html=self.article.body_html,
            parent_id=self.site.articles_parent_id,
            meta_description=self.article.meta_description,
            meta_keywords=self.article.meta_keywords,
        )
        self.article.remote_page_id = page["id"]
        self.article.remote_url = f"{self.site.base_url}{page.get('url', '')}"
        self.db.commit()
        return page

    def _attach_cover(self, page_id: int) -> None:
        style = (self.site.cover_style_prompt if self.site.cover_mode == "prompt"
                 else "в стиле уже существующих обложек этого сайта")
        prompt = self._image_prompt("cover", {"topic": self.article.topic,
                                              "cover_style": style})
        result = self.image_generator.generate(
            prompt=prompt, size=self.image_params["size"],
            quality=self.image_params["quality"], crop=COVER_CROP)
        filename = image_filename(self.article.id, 0)
        self.site_client.set_page_cover(page_id, result.data, filename)
        self.db.add(ArticleImage(article_id=self.article.id, kind="cover", position=0,
                                 prompt=prompt, remote_path=filename, cost=result.cost))
        self._record_usage("image", 0, 0, result.cost)
        self.db.commit()

    def _record_usage(self, kind: str, tokens_prompt: int, tokens_completion: int,
                      cost: float) -> None:
        if self.job_run_id is None:
            return
        self.db.add(LlmUsage(job_run_id=self.job_run_id, kind=kind,
                             model=getattr(self.text_client, "model", ""),
                             tokens_prompt=tokens_prompt,
                             tokens_completion=tokens_completion, cost=cost))


def build_for(db: Session, article: Article, site: Site, site_client,
              job_run_id: int | None) -> None:
    """Сборка билдера из настроек БД — точка входа для Celery-задачи."""
    watermark = b""
    if site.watermark_path:
        try:
            with open(site.watermark_path, "rb") as f:
                watermark = f.read()
        except OSError:
            watermark = b""

    ArticleBuilder(
        db=db, article=article, site=site,
        text_client=build_text_client(db),
        image_generator=build_image_generator(db),
        site_client=site_client,
        image_params=image_params(db),
        watermark_bytes=watermark,
        job_run_id=job_run_id,
    ).build()
```

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -v`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/articles/builder.py execution/backend/tests/test_articles_builder.py
git commit -m "feat: сборка статьи — текст, картинки, публикация черновиком"
```

---

### Task 17: Celery-задачи

**Files:**
- Create: `execution/backend/app/celery_app.py`
- Create: `execution/backend/app/tasks.py`
- Test: `execution/backend/tests/test_tasks.py`

- [ ] **Step 1: Написать падающий тест**

Задачи тестируются вызовом их внутренних функций напрямую — брокер в тестах не поднимается.

`execution/backend/tests/test_tasks.py`:

```python
from types import SimpleNamespace

import pytest

from app.ai.text import JsonResult
from app.models.article import Article, ArticleBatch
from app.models.job import JobRun
from app.models.site import Site
from app.tasks import run_batch_sync, generate_topics_sync


@pytest.fixture
def site(db_session):
    from app.seed import seed_prompts

    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="x.ru", base_url="https://x.ru",
               api_token_enc="e", articles_parent_id=25,
               articles_url_prefix="/poleznye-stati/",
               site_description="Стройбаза в Самаре", tone_of_voice="практичный",
               reference_article_id=312, reference_html="<p>эталон</p><img><img>",
               reference_images=2)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def batch(db_session, site, admin):
    row = ArticleBatch(site_id=site.id, requested_count=3, created_by_id=admin.id)
    db_session.add(row)
    db_session.commit()
    return row


def patch_deps(monkeypatch, topics, existing=None):
    monkeypatch.setattr(
        "app.tasks.build_text_client",
        lambda db: SimpleNamespace(model="m",
                                   complete_json=lambda p: JsonResult(topics, 10, 20, 0.2)))
    monkeypatch.setattr(
        "app.tasks.open_site_client",
        lambda db, site: SimpleNamespace(
            list_section_pages=lambda prefix: existing or []))


def test_generate_topics_fills_articles(db_session, batch, monkeypatch):
    patch_deps(monkeypatch, ["Тема А", "Тема Б", "Тема В"])
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "topics_review"
    assert [a.topic for a in batch.articles] == ["Тема А", "Тема Б", "Тема В"]


def test_generate_topics_drops_duplicates(db_session, batch, monkeypatch):
    patch_deps(monkeypatch, ["Чем утеплить каркасный дом", "Как выбрать кровлю"],
               existing=[{"title": "Чем утеплить каркасный дом", "url": "/blog/a/"}])
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert [a.topic for a in batch.articles] == ["Как выбрать кровлю"]


def test_generate_topics_records_job_run(db_session, batch, monkeypatch):
    patch_deps(monkeypatch, ["Тема А"])
    generate_topics_sync(db_session, batch.id)
    job = db_session.query(JobRun).filter_by(kind="generate_topics").one()
    assert job.status == "ok"
    assert job.finished_at is not None


def test_generate_topics_failure_marks_batch(db_session, batch, monkeypatch):
    from app.ai.text import LLMError

    def broken(db):
        raise LLMError("LLM недоступна после 3 попыток")

    monkeypatch.setattr("app.tasks.build_text_client", broken)
    monkeypatch.setattr("app.tasks.open_site_client",
                        lambda db, site: SimpleNamespace(list_section_pages=lambda p: []))
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "недоступна" in batch.error_text


def test_non_list_answer_marks_batch_failed(db_session, batch, monkeypatch):
    patch_deps(monkeypatch, {"topics": ["Тема"]})
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "массив" in batch.error_text


def test_run_batch_builds_each_article(db_session, batch, site, monkeypatch):
    db_session.add_all([
        Article(batch_id=batch.id, site_id=site.id, topic="Тема А"),
        Article(batch_id=batch.id, site_id=site.id, topic="Тема Б"),
    ])
    batch.status = "topics_review"
    db_session.commit()

    built = []
    monkeypatch.setattr("app.tasks.build_for",
                        lambda db, article, site, site_client, job_run_id:
                        built.append(article.topic) or setattr(article, "status", "published"))
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    run_batch_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert built == ["Тема А", "Тема Б"]
    assert batch.status == "done"


def test_run_batch_continues_after_single_failure(db_session, batch, site, monkeypatch):
    db_session.add_all([
        Article(batch_id=batch.id, site_id=site.id, topic="Тема А"),
        Article(batch_id=batch.id, site_id=site.id, topic="Тема Б"),
    ])
    batch.status = "topics_review"
    db_session.commit()

    def build(db, article, site, site_client, job_run_id):
        article.status = "failed" if article.topic == "Тема А" else "published"

    monkeypatch.setattr("app.tasks.build_for", build)
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    run_batch_sync(db_session, batch.id)
    db_session.refresh(batch)
    # Одна упавшая статья не должна отменять остальные — партия всё равно done.
    assert batch.status == "done"
    assert {a.status for a in batch.articles} == {"failed", "published"}
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_tasks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tasks'`

- [ ] **Step 3: Объект Celery**

`execution/backend/app/celery_app.py`:

```python
from celery import Celery

from app.config import config

# include, а не импорт app.tasks на уровне модуля: tasks.py сам импортирует
# отсюда celery_app, и прямой импорт дал бы цикл. Без include worker
# поднимается, но не знает ни одной задачи — «Received unregistered task».
celery_app = Celery(
    "content", broker=config.redis_url, backend=config.redis_url,
    include=["app.tasks"],
)
celery_app.conf.timezone = "Europe/Samara"

# beat не нужен: периодических задач нет, всё запускается из UI.

# Без лимита зависшее соединение с RouterAI держит слот воркера сколько
# угодно долго, а воркеров всего два (--concurrency=2, см. docker-compose.yml),
# то есть одна такая задача съедает половину мощности.
#
# Расчёт худшего случая одного вызова LLM: REQUEST_TIMEOUT_SECONDS=120 с на
# попытку × llm_max_retries (дефолт 3) плюс паузы backoff 2 с и 4 с ≈ 366 с.
#
# Эти глобальные лимиты рассчитаны на задачи с предсказуемой длительностью —
# generate_topics и retry_article: один вызов LLM плюс сборка одной статьи с
# картинками. Для run_batch они НЕ применяются: партия обрабатывается
# последовательно и её длительность пропорциональна числу статей, поэтому
# лимит вычисляется при постановке задачи и передаётся в apply_async()
# (см. Task 18). Глухой статический лимит там обрывал бы законную работу на
# середине партии — часть статей опубликована, часть нет, — что хуже, чем
# его отсутствие: защищаться нужно от зависшего соединения, а не от штатной
# нагрузки.
#
# Мягкий лимит меньше жёсткого на 3 минуты: этого хватает, чтобы обработчик
# SoftTimeLimitExceeded (см. tasks.py) записал status="failed" в JobRun и
# ArticleBatch и закрыл сессию БД до принудительного убийства процесса.
celery_app.conf.task_soft_time_limit = 900   # 15 минут
celery_app.conf.task_time_limit = 1080       # 18 минут
```

- [ ] **Step 4: Задачи**

`execution/backend/app/tasks.py`:

```python
"""Фоновые задачи. Каждая обёрнута парой sync-функций: сама задача открывает
сессию, а логика живёт в `*_sync(db, ...)` — так её можно тестировать без брокера.
"""

from __future__ import annotations

from celery.exceptions import SoftTimeLimitExceeded

from app.ai.factory import build_text_client
from app.ai.prompts import PromptError, render_prompt, resolve_prompt
from app.ai.text import LLMError
from app.api.admin_sites import open_client as open_site_client
from app.articles.builder import build_for
from app.articles.topics import filter_duplicates
from app.celery_app import celery_app
from app.clock import utcnow
from app.db import SessionLocal
from app.models.article import Article, ArticleBatch
from app.models.job import JobRun
from app.models.site import Site
from app.settings.crypto import SecretDecryptionError
from app.sites.client import SiteAPIError


def _start_job(db, kind: str, site_id: int, created_by_id: int | None,
               params: dict) -> JobRun:
    job = JobRun(kind=kind, site_id=site_id, created_by_id=created_by_id,
                 params_json=params, status="running")
    db.add(job)
    db.commit()
    return job


def _finish_job(db, job: JobRun, status: str, log: str = "") -> None:
    job.status = status
    job.log_text = log
    job.finished_at = utcnow()
    db.commit()


# --- генерация тем ---

def generate_topics_sync(db, batch_id: int) -> None:
    batch = db.get(ArticleBatch, batch_id)
    site = db.get(Site, batch.site_id)
    job = _start_job(db, "generate_topics", site.id, batch.created_by_id,
                     {"batch_id": batch_id, "count": batch.requested_count})
    try:
        existing = [p.get("title", "") for p in
                    open_site_client(db, site).list_section_pages(site.articles_url_prefix)]
        template = resolve_prompt(db, "topics", site.id)
        prompt = render_prompt(template, {
            "count": batch.requested_count,
            "site_name": site.name,
            # site_description и tone_of_voice шаблон topics использует, но
            # раньше их сюда не передавали — с дефолтным Undefined они молча
            # рендерились пустотой, и тематика сайта не доезжала до модели.
            # Ровно против этого промаха поля и заведены (см. app/models/site.py).
            "site_description": site.site_description,
            "tone_of_voice": site.tone_of_voice,
            "existing_titles": existing,
        })
        result = build_text_client(db).complete_json(prompt)
        if not isinstance(result.data, list):
            raise LLMError("модель вернула не массив тем")

        proposed = [str(t).strip() for t in result.data if str(t).strip()]
        kept, dropped = filter_duplicates(proposed, existing)
        for topic in kept:
            db.add(Article(batch_id=batch.id, site_id=site.id, topic=topic))
        batch.status = "topics_review"
        db.commit()
        _finish_job(db, job, "ok",
                    f"предложено {len(proposed)}, отсеяно дублей {len(dropped)}, "
                    f"принято {len(kept)}")
    # SoftTimeLimitExceeded — в том же списке: без него мягкий лимит бесполезен,
    # задача умрёт по жёсткому, а JobRun навсегда останется в статусе "running",
    # и в журнале это выглядит как «задача до сих пор идёт».
    except (LLMError, PromptError, SiteAPIError, SecretDecryptionError,
            SoftTimeLimitExceeded) as exc:
        batch.status = "failed"
        batch.error_text = str(exc) or "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", str(exc) or "превышен лимит времени задачи")


@celery_app.task(name="app.tasks.generate_topics")
def generate_topics(batch_id: int) -> None:
    db = SessionLocal()
    try:
        generate_topics_sync(db, batch_id)
    finally:
        db.close()


# --- сборка партии ---

def run_batch_sync(db, batch_id: int) -> None:
    batch = db.get(ArticleBatch, batch_id)
    site = db.get(Site, batch.site_id)
    batch.status = "running"
    db.commit()

    job = _start_job(db, "run_batch", site.id, batch.created_by_id,
                     {"batch_id": batch_id, "articles": len(batch.articles)})
    site_client = open_site_client(db, site)

    try:
        for article in batch.articles:
            if article.status == "published":
                continue
            # Падение одной статьи не должно отменять остальные: билдер сам пишет
            # причину в error_text и оставляет статью в failed.
            build_for(db, article, site, site_client, job.id)
            db.commit()
    except SoftTimeLimitExceeded:
        # Лимит вычисляется от числа статей (см. Task 18), так что сюда мы
        # попадаем только при реально зависшей партии. Уже опубликованные
        # статьи остаются опубликованными — их пропустит `continue` при
        # повторном запуске; помечаем партию, чтобы она не висела в "running".
        done = len([a for a in batch.articles if a.status == "published"])
        batch.status = "failed"
        batch.error_text = (f"превышен лимит времени партии, готово "
                            f"{done}/{len(batch.articles)}")
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        return

    batch.status = "done"
    db.commit()
    failed = [a for a in batch.articles if a.status == "failed"]
    _finish_job(db, job, "ok" if not failed else "failed",
                f"готово {len(batch.articles) - len(failed)}/{len(batch.articles)}")


@celery_app.task(name="app.tasks.run_batch")
def run_batch(batch_id: int) -> None:
    db = SessionLocal()
    try:
        run_batch_sync(db, batch_id)
    finally:
        db.close()


# --- повтор одной статьи ---

def retry_article_sync(db, article_id: int) -> None:
    article = db.get(Article, article_id)
    site = db.get(Site, article.site_id)
    job = _start_job(db, "retry_article", site.id, None, {"article_id": article_id})
    try:
        build_for(db, article, site, open_site_client(db, site), job.id)
    except SoftTimeLimitExceeded:
        article.status = "failed"
        article.error_text = "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", article.error_text)
        return
    db.commit()
    _finish_job(db, job, "ok" if article.status == "published" else "failed",
                article.error_text)


@celery_app.task(name="app.tasks.retry_article")
def retry_article(article_id: int) -> None:
    db = SessionLocal()
    try:
        retry_article_sync(db, article_id)
    finally:
        db.close()
```

- [ ] **Step 5: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_tasks.py -v`
Expected: PASS — 7 passed

- [ ] **Step 6: Проверить, что worker видит задачи**

Run: `cd execution && docker compose up -d worker && docker compose logs worker | grep -A5 "\[tasks\]"`
Expected: в списке `[tasks]` присутствуют `app.tasks.generate_topics`, `app.tasks.run_batch`, `app.tasks.retry_article`

- [ ] **Step 7: Commit**

```bash
git add execution/backend/app/celery_app.py execution/backend/app/tasks.py execution/backend/tests/test_tasks.py
git commit -m "feat: фоновые задачи генерации тем и сборки партии"
```

---

### Task 18: API партий, статей и журнала

**Files:**
- Create: `execution/backend/app/api/article_batches.py`
- Create: `execution/backend/app/api/jobs.py`
- Create: `execution/backend/app/api/tasks_status.py`
- Modify: `execution/backend/app/main.py`
- Test: `execution/backend/tests/test_api_batches.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_api_batches.py`:

```python
import pytest


@pytest.fixture
def site_id(admin_client, db_session):
    from app.models.site import Site

    site = Site(name="Стройбаза", domain="x.ru", base_url="https://x.ru",
                api_token_enc="e", articles_parent_id=25,
                articles_url_prefix="/poleznye-stati/",
                reference_html="<p>эталон</p><img><img>", reference_images=2)
    db_session.add(site)
    db_session.commit()
    return site.id


@pytest.fixture
def no_celery(monkeypatch):
    sent = []
    monkeypatch.setattr("app.api.article_batches.generate_topics.delay",
                        lambda batch_id: sent.append(("topics", batch_id)) or
                        type("R", (), {"id": "task-1"})())
    # run_batch ставится через apply_async с вычисленными лимитами времени,
    # а не через delay — подменяем именно его.
    monkeypatch.setattr("app.api.article_batches.run_batch.apply_async",
                        lambda args, **kwargs: sent.append(("run", args[0], kwargs)) or
                        type("R", (), {"id": "task-2"})())
    return sent


def test_batch_time_limits_grow_with_article_count():
    """Лимит партии считается от числа статей: иначе большая партия
    обрывается на середине по глухому статическому лимиту."""
    from app.api.article_batches import _batch_time_limits

    soft_one, hard_one = _batch_time_limits(1)
    soft_many, _ = _batch_time_limits(50)
    assert soft_many > soft_one
    assert hard_one > soft_one          # мягкий раньше жёсткого
    assert _batch_time_limits(10_000)[0] <= 6 * 60 * 60   # потолок держит


def test_manager_creates_batch(manager_client, site_id, no_celery):
    resp = manager_client.post("/api/article-batches",
                               json={"site_id": site_id, "count": 5})
    assert resp.status_code == 200
    assert resp.json()["status"] == "topics_pending"
    assert no_celery == [("topics", resp.json()["id"])]


def test_batch_requires_auth(client, site_id):
    assert client.post("/api/article-batches",
                       json={"site_id": site_id, "count": 5}).status_code == 401


def test_unknown_site_rejected(manager_client, no_celery):
    resp = manager_client.post("/api/article-batches", json={"site_id": 999, "count": 5})
    assert resp.status_code == 404


def test_count_is_bounded(manager_client, site_id, no_celery):
    assert manager_client.post("/api/article-batches",
                               json={"site_id": site_id, "count": 0}).status_code == 422
    assert manager_client.post("/api/article-batches",
                               json={"site_id": site_id, "count": 51}).status_code == 422


def test_batch_detail_lists_articles(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 2}).json()["id"]
    db_session.add(Article(batch_id=batch_id, site_id=site_id, topic="Тема А"))
    db_session.commit()

    body = manager_client.get(f"/api/article-batches/{batch_id}").json()
    assert body["site_name"] == "Стройбаза"
    assert [a["topic"] for a in body["articles"]] == ["Тема А"]


def test_topics_can_be_edited_before_run(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 2}).json()["id"]
    db_session.add(Article(batch_id=batch_id, site_id=site_id, topic="Старая тема"))
    db_session.commit()
    db_session.query(type(db_session.get(Article, 1))).count()

    resp = manager_client.put(f"/api/article-batches/{batch_id}/topics",
                              json={"topics": ["Новая А", "Новая Б"]})
    assert resp.status_code == 200
    assert [a["topic"] for a in resp.json()["articles"]] == ["Новая А", "Новая Б"]


def test_topics_cannot_be_edited_after_run(manager_client, db_session, site_id, no_celery):
    from app.models.article import ArticleBatch

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 2}).json()["id"]
    db_session.get(ArticleBatch, batch_id).status = "running"
    db_session.commit()

    resp = manager_client.put(f"/api/article-batches/{batch_id}/topics",
                              json={"topics": ["Поздно"]})
    assert resp.status_code == 400


def test_run_requires_topics(manager_client, site_id, no_celery):
    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 2}).json()["id"]
    resp = manager_client.post(f"/api/article-batches/{batch_id}/run")
    assert resp.status_code == 400
    assert "тем" in resp.json()["detail"]


def test_run_starts_task(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    db_session.add(Article(batch_id=batch_id, site_id=site_id, topic="Тема"))
    db_session.commit()

    resp = manager_client.post(f"/api/article-batches/{batch_id}/run")
    assert resp.status_code == 200
    assert ("run", batch_id) in no_celery


def test_jobs_list_shows_cost(manager_client, db_session, site_id):
    from app.models.job import JobRun, LlmUsage

    job = JobRun(kind="run_batch", site_id=site_id, params_json={}, status="ok")
    db_session.add(job)
    db_session.commit()
    db_session.add_all([LlmUsage(job_run_id=job.id, kind="image", cost=5.4),
                        LlmUsage(job_run_id=job.id, kind="text", cost=0.6)])
    db_session.commit()

    body = manager_client.get("/api/jobs").json()
    assert body[0]["kind"] == "run_batch"
    assert body[0]["cost"] == pytest.approx(6.0)
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_batches.py -v`
Expected: FAIL — 404 на `/api/article-batches`

- [ ] **Step 3: Роутер партий**

`execution/backend/app/api/article_batches.py`:

```python
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.article import Article, ArticleBatch
from app.models.site import Site
from app.models.user import User
from app.tasks import generate_topics, retry_article, run_batch

router = APIRouter(prefix="/api", tags=["articles"])

EDITABLE_STATUSES = {"topics_pending", "topics_review", "failed"}


class BatchIn(BaseModel):
    site_id: int
    count: int = Field(ge=1, le=50)


class ArticleOut(BaseModel):
    id: int
    topic: str
    title: str
    status: str
    remote_url: str
    error_text: str


class BatchOut(BaseModel):
    id: int
    site_id: int
    site_name: str
    site_domain: str
    requested_count: int
    status: str
    error_text: str
    created_at: datetime
    articles: list[ArticleOut] = []


def _to_out(db: Session, batch: ArticleBatch) -> BatchOut:
    site = db.get(Site, batch.site_id)
    return BatchOut(
        id=batch.id, site_id=batch.site_id,
        site_name=site.name if site else "—",
        site_domain=site.domain if site else "—",
        requested_count=batch.requested_count, status=batch.status,
        error_text=batch.error_text, created_at=batch.created_at,
        articles=[ArticleOut(id=a.id, topic=a.topic, title=a.title, status=a.status,
                             remote_url=a.remote_url, error_text=a.error_text)
                  for a in batch.articles],
    )


def _get_or_404(db: Session, batch_id: int) -> ArticleBatch:
    batch = db.get(ArticleBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "партия не найдена")
    return batch


@router.get("/article-batches", response_model=list[BatchOut])
def list_batches(db: Session = Depends(get_db),
                 _user: User = Depends(get_current_user)):
    batches = db.scalars(select(ArticleBatch).order_by(ArticleBatch.id.desc())).all()
    return [_to_out(db, b) for b in batches]


@router.post("/article-batches", response_model=BatchOut)
def create_batch(payload: BatchIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    if db.get(Site, payload.site_id) is None:
        raise HTTPException(404, "сайт не найден")
    batch = ArticleBatch(site_id=payload.site_id, requested_count=payload.count,
                         created_by_id=user.id)
    db.add(batch)
    db.commit()
    generate_topics.delay(batch.id)
    return _to_out(db, batch)


@router.get("/article-batches/{batch_id}", response_model=BatchOut)
def read_batch(batch_id: int, db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)):
    return _to_out(db, _get_or_404(db, batch_id))


# Бюджет времени на одну статью в партии. Складывается из худшего случая
# текстовых вызовов (≈366 с на один вызов: 120 с таймаута × 3 попытки плюс
# паузы backoff, см. app/ai/text.py; вызовов на статью несколько — тело,
# промпт на каждую картинку, промпт обложки, — но они не суммируются в этот
# бюджет впритык, а покрываются тем же запасом, что и публикация) плюс
# худший случай пачки картинок, которые генерируются параллельно
# (`ThreadPoolExecutor`, см. app/ai/images.py) — тоже ≈365 с на пачку, а не
# на картинку, — плюс запас на публикацию. Это граница «задача зависла», а
# не ожидаемая длительность: типовая статья укладывается в разы быстрее.
# Раньше здесь стояло 420 — ровно столько же, сколько был таймаут ОДНОЙ
# попытки генерации ОДНОЙ картинки (TIMEOUT=420 в старой версии
# app/ai/images.py, до трёх попыток с retry — то есть до ≈1275 с на одну
# картинку). Ревью Task 8 показало и посчитало это несоответствие; заодно
# TIMEOUT там снижен до 180, а max_retries — до 2 (см. Task 8, Step 8).
ARTICLE_TIME_BUDGET_SECONDS = 900
# Запас на подготовку: открытие клиента сайта, чтение эталона, разбор списка.
BATCH_OVERHEAD_SECONDS = 300
# Потолок на случай, если ограничение числа статей в партии когда-нибудь
# ослабят: без него опечатка в количестве поставила бы задачу на сутки.
# ВНИМАНИЕ (открытый вопрос ревью Task 8): при текущем максимуме партии в 50
# статей (см. test_count_is_bounded) этот потолок связывает бюджет:
# soft = min(300 + 900×50, 21600) = 21600, то есть на партию из 50 статей
# приходится ≈432 с на статью (21600/50), а резать бюджет потолок начинает
# уже примерно с 24 статей.
#
# Это осознанное решение, а не недосмотр. ARTICLE_TIME_BUDGET_SECONDS = 900 —
# граница «статья зависла», а не ожидаемая длительность: типовая статья
# укладывается в 2–4 минуты, то есть партия из 50 штук проходит за 2–3 часа
# и до потолка не доходит. Упереться в него можно только если зависла не одна
# статья, а значительная часть партии.
#
# Ключевое: упереться в потолок не разрушительно. Обработчик
# SoftTimeLimitExceeded (Task 17) помечает партию как failed — не как running,
# — с указанием, сколько статей успело опубликоваться; повторный запуск
# разрешён (эндпоинт run отклоняет только status="running"), а run_batch_sync
# пропускает уже опубликованные статьи. То есть партия продолжается с места
# остановки, а не начинается заново и не оплачивается повторно.
#
# Поднимать потолок до ~12.6 часа (300 + 900×50) было бы хуже: воркеров всего
# два, и одна задача, держащая слот полсуток, останавливает работу остальных
# надолго. Оборвать и продолжить дешевле, чем ждать.
BATCH_TIME_LIMIT_CAP_SECONDS = 6 * 60 * 60
# Разрыв между мягким и жёстким лимитом: столько есть у обработчика
# SoftTimeLimitExceeded в tasks.py, чтобы записать отказ в журнал и закрыть
# сессию БД до принудительного завершения процесса.
TIME_LIMIT_GAP_SECONDS = 180


def _batch_time_limits(article_count: int) -> tuple[int, int]:
    soft = min(BATCH_OVERHEAD_SECONDS + ARTICLE_TIME_BUDGET_SECONDS * article_count,
               BATCH_TIME_LIMIT_CAP_SECONDS)
    return soft, soft + TIME_LIMIT_GAP_SECONDS


class TopicsIn(BaseModel):
    topics: list[str]


@router.put("/article-batches/{batch_id}/topics", response_model=BatchOut)
def save_topics(batch_id: int, payload: TopicsIn, db: Session = Depends(get_db),
                _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if batch.status not in EDITABLE_STATUSES:
        raise HTTPException(400, "темы уже отправлены в работу — правка невозможна")

    # Согласованный список заменяет предложенный целиком: менеджер мог
    # переписать формулировки, а не только вычеркнуть лишнее.
    for article in list(batch.articles):
        db.delete(article)
    db.flush()
    for topic in [t.strip() for t in payload.topics if t.strip()]:
        db.add(Article(batch_id=batch.id, site_id=batch.site_id, topic=topic))
    batch.status = "topics_review"
    db.commit()
    db.refresh(batch)
    return _to_out(db, batch)


@router.post("/article-batches/{batch_id}/run", response_model=BatchOut)
def run(batch_id: int, db: Session = Depends(get_db),
        _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if not batch.articles:
        raise HTTPException(400, "в партии нет тем")
    if batch.status == "running":
        raise HTTPException(400, "партия уже выполняется")
    # Лимит времени вычисляется здесь, а не берётся из глобальной настройки
    # Celery: партия идёт последовательно, и её длительность пропорциональна
    # числу статей. Глухой статический лимит обрывал бы работу на середине —
    # часть статей опубликована, часть нет.
    soft, hard = _batch_time_limits(len(batch.articles))
    run_batch.apply_async(args=[batch.id], soft_time_limit=soft, time_limit=hard)
    return _to_out(db, batch)


@router.post("/articles/{article_id}/retry")
def retry(article_id: int, db: Session = Depends(get_db),
          _user: User = Depends(get_current_user)):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "статья не найдена")
    if article.status == "published":
        raise HTTPException(400, "статья уже выложена черновиком")
    retry_article.delay(article.id)
    return {"ok": True}
```

- [ ] **Step 4: Роутеры журнала и статуса задач**

`execution/backend/app/api/jobs.py`:

```python
"""Журнал задач. `cost` — сумма LlmUsage.cost (Task 7/14), а это, в свою
очередь, usage.cost из ответа RouterAI — нестандартное расширение, которого
может не быть у конкретной модели. Ноль в этом поле не обязательно значит
«бесплатно»: если провайдер его не прислал, TextClient пишет предупреждение
в лог (app/ai/text.py, _usage) и подставляет 0. Отличить «правда бесплатно»
от «не сообщили» по одной только цифре в журнале нельзя — при подозрении
смотреть логи воркера за нужный job_run_id."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.job import JobRun
from app.models.site import Site
from app.models.user import User

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobOut(BaseModel):
    id: int
    kind: str
    site_name: str
    status: str
    log_text: str
    cost: float
    tokens_total: int
    started_at: datetime
    finished_at: datetime | None


@router.get("", response_model=list[JobOut])
def list_jobs(limit: int = 100, offset: int = 0, db: Session = Depends(get_db),
              _user: User = Depends(get_current_user)):
    jobs = db.scalars(
        select(JobRun).order_by(JobRun.id.desc()).limit(limit).offset(offset)).all()
    result = []
    for job in jobs:
        site = db.get(Site, job.site_id) if job.site_id else None
        result.append(JobOut(
            id=job.id, kind=job.kind, site_name=site.name if site else "—",
            status=job.status, log_text=job.log_text,
            # 0 может означать «провайдер не сообщил стоимость», а не «бесплатно».
            cost=sum(u.cost for u in job.usage),
            tokens_total=sum(u.tokens_prompt + u.tokens_completion for u in job.usage),
            started_at=job.started_at, finished_at=job.finished_at,
        ))
    return result
```

`execution/backend/app/api/tasks_status.py`:

```python
"""Опрос состояния фоновой задачи Celery. Не привязан к конкретной задаче:
любая, чей task_id известен фронту, опрашивается одинаково."""

from celery.result import AsyncResult
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.celery_app import celery_app
from app.models.user import User

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskStatus(BaseModel):
    state: str
    result: object | None = None


@router.get("/{task_id}/status", response_model=TaskStatus)
def task_status(task_id: str, _user: User = Depends(get_current_user)):
    result = AsyncResult(task_id, app=celery_app)
    return TaskStatus(state=result.state, result=result.result if result.ready() else None)
```

- [ ] **Step 5: Подключить роутеры**

Финальный `execution/backend/app/main.py`:

```python
from fastapi import FastAPI

from app.api import (
    admin_prompts,
    admin_settings,
    admin_sites,
    admin_users,
    article_batches,
    auth,
    jobs,
    sites,
    tasks_status,
)

app = FastAPI(title="k1 content service")

for module in (auth, sites, admin_sites, admin_settings, admin_prompts, admin_users,
               article_batches, jobs, tasks_status):
    app.include_router(module.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
```

`admin_users` создаётся в задаче 19 — до тех пор не включай его в кортеж и в импорт.

- [ ] **Step 6: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_batches.py -v`
Expected: PASS — 10 passed

- [ ] **Step 7: Прогнать весь бэкенд**

Run: `cd execution && docker compose run --rm --no-deps backend pytest -q`
Expected: PASS — все тесты зелёные

- [ ] **Step 8: Commit**

```bash
git add execution/backend/app/api execution/backend/tests/test_api_batches.py
git commit -m "feat: API партий статей, повтора и журнала задач"
```

---

### Task 19: API пользователей

**Files:**
- Create: `execution/backend/app/api/admin_users.py`
- Modify: `execution/backend/app/main.py`
- Test: `execution/backend/tests/test_api_admin_users.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_api_admin_users.py`:

```python
def test_manager_cannot_list_users(manager_client):
    assert manager_client.get("/api/admin/users").status_code == 403


def test_admin_lists_users(admin_client, manager):
    body = admin_client.get("/api/admin/users").json()
    assert {u["email"] for u in body} == {"admin@k1.ru", "manager@k1.ru"}


def test_password_hash_never_returned(admin_client, manager):
    body = admin_client.get("/api/admin/users").json()
    assert all("password" not in key for user in body for key in user)


def test_admin_creates_manager(admin_client):
    resp = admin_client.post("/api/admin/users", json={
        "email": "new@k1.ru", "full_name": "Новый", "role": "manager",
        "password": "password123"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "manager"


def test_duplicate_email_rejected(admin_client, manager):
    resp = admin_client.post("/api/admin/users", json={
        "email": "manager@k1.ru", "full_name": "Дубль", "role": "manager",
        "password": "password123"})
    assert resp.status_code == 400


def test_short_password_rejected(admin_client):
    resp = admin_client.post("/api/admin/users", json={
        "email": "x@k1.ru", "full_name": "X", "role": "manager", "password": "123"})
    assert resp.status_code == 422


def test_empty_password_on_update_keeps_current(admin_client, manager, client):
    admin_client.put(f"/api/admin/users/{manager.id}", json={
        "email": "manager@k1.ru", "full_name": "Переименован", "role": "manager",
        "password": "", "is_active": True})
    resp = client.post("/api/auth/login",
                       data={"username": "manager@k1.ru", "password": "managerpass"})
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Переименован"


def test_last_admin_cannot_be_deleted(admin_client, admin):
    resp = admin_client.delete(f"/api/admin/users/{admin.id}")
    assert resp.status_code == 400
    assert "последн" in resp.json()["detail"]


def test_last_admin_cannot_be_demoted(admin_client, admin):
    resp = admin_client.put(f"/api/admin/users/{admin.id}", json={
        "email": "admin@k1.ru", "full_name": "Админ", "role": "manager",
        "password": "", "is_active": True})
    assert resp.status_code == 400


def test_manager_can_be_deleted(admin_client, manager):
    assert admin_client.delete(f"/api/admin/users/{manager.id}").status_code == 200
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_users.py -v`
Expected: FAIL — 404 на `/api/admin/users`

- [ ] **Step 3: Реализация**

`execution/backend/app/api/admin_users.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.api.security import hash_password
from app.models.user import User

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])

ROLES = ("admin", "manager")


class UserIn(BaseModel):
    email: str
    full_name: str
    role: str = "manager"
    password: str = Field(default="", min_length=0)   # пусто при правке = «не менять»
    is_active: bool = True


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool


def _to_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, full_name=user.full_name,
                   role=user.role, is_active=user.is_active)


def _count_active_admins(db: Session, exclude_id: int | None = None) -> int:
    query = select(func.count()).select_from(User).where(
        User.role == "admin", User.is_active.is_(True))
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    return db.scalar(query) or 0


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _user: User = Depends(require_role("admin"))):
    return [_to_out(u) for u in db.scalars(select(User).order_by(User.email)).all()]


@router.post("", response_model=UserOut)
def create_user(payload: UserIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    if payload.role not in ROLES:
        raise HTTPException(400, f"неизвестная роль: {payload.role}")
    if len(payload.password) < 8:
        raise HTTPException(422, "пароль короче 8 символов")
    # Нормализация зеркалит login() и create_admin.py: колонка email
    # регистрозависима, поэтому без .lower() «Ivan@k1.ru» и «ivan@k1.ru»
    # завелись бы как два разных пользователя (unique=True их не различает),
    # а войти удалось бы только тем написанием, которым создавали.
    email = payload.email.strip().lower()
    if db.scalars(select(User).where(User.email == email)).first():
        raise HTTPException(400, f"пользователь {email} уже существует")

    try:
        password_hash = hash_password(payload.password)
    except ValueError as e:
        # hash_password бросает ValueError на пароле длиннее 72 байт —
        # без перехвата это ушло бы наружу 500-й (см. app/api/security.py).
        raise HTTPException(422, str(e))

    user = User(email=email, full_name=payload.full_name, role=payload.role,
                is_active=payload.is_active,
                password_hash=password_hash)
    db.add(user)
    db.commit()
    return _to_out(user)


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "пользователь не найден")
    if payload.role not in ROLES:
        raise HTTPException(400, f"неизвестная роль: {payload.role}")

    # Снятие роли или деактивация последнего админа заперла бы админку снаружи —
    # починить это можно было бы только руками в БД.
    losing_admin = user.role == "admin" and (payload.role != "admin" or not payload.is_active)
    if losing_admin and _count_active_admins(db, exclude_id=user.id) == 0:
        raise HTTPException(400, "это последний активный администратор")

    # Тот же .lower(), что и в create_user — иначе правка пользователя стала бы
    # обходным путём завести адрес в другом регистре. Занятость проверяем до
    # присваивания: без этой проверки смена почты на чужую упала бы нарушением
    # unique-констрейнта, то есть 500-й вместо внятного 400.
    email = payload.email.strip().lower()
    if email != user.email and db.scalars(select(User).where(User.email == email)).first():
        raise HTTPException(400, f"пользователь {email} уже существует")

    user.email = email
    user.full_name = payload.full_name
    user.role = payload.role
    user.is_active = payload.is_active
    if payload.password:
        if len(payload.password) < 8:
            raise HTTPException(422, "пароль короче 8 символов")
        try:
            user.password_hash = hash_password(payload.password)
        except ValueError as e:
            raise HTTPException(422, str(e))
    db.commit()
    return _to_out(user)


@router.delete("/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "пользователь не найден")
    if user.role == "admin" and _count_active_admins(db, exclude_id=user.id) == 0:
        raise HTTPException(400, "это последний активный администратор")
    db.delete(user)
    db.commit()
    return {"ok": True}
```

`create_user`/`update_user` оборачивают `hash_password` в `try/except
ValueError` (правка вслед за ревью Task 3): `hash_password` бросает
`ValueError`, если пароль длиннее 72 байт — bcrypt иначе молча обрезал бы
ввод, делая длинные пароли с общим префиксом взаимозаменяемыми. Без перехвата
это ушло бы наружу как 500, а не как понятная 422-ошибка валидации.

- [ ] **Step 4: Подключить роутер**

Раскомментируй `admin_users` в импорте и в кортеже `main.py` (см. Task 18, Step 5).

- [ ] **Step 5: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_users.py -v`
Expected: PASS — 10 passed

- [ ] **Step 6: Commit**

```bash
git add execution/backend/app/api/admin_users.py execution/backend/app/main.py execution/backend/tests/test_api_admin_users.py
git commit -m "feat: API пользователей с защитой последнего администратора"
```

---

## Фаза 5 — Фронтенд

Модульных тестов здесь нет — как в обоих образцах. Каждая задача заканчивается ручной
проверкой через дев-сервер: `docker compose up api frontend`, затем `http://localhost:3000`.

### Task 20: Каркас фронтенда и тема

**Files:**
- Create: `execution/frontend/package.json`, `tsconfig.json`, `tsconfig.node.json`, `vite.config.ts`, `index.html`
- Create: `execution/frontend/src/main.tsx`, `index.css`, `App.tsx`, `auth.tsx`, `api.ts`

- [ ] **Step 1: package.json**

`execution/frontend/package.json` — состав ровно как в `nst-tg-monitor`:

```json
{
  "name": "k1-content-frontend",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.23.1",
    "antd": "^5.17.4",
    "@ant-design/icons": "^5.3.7",
    "axios": "^1.7.2",
    "dayjs": "^1.11.11"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.4.5",
    "vite": "^5.2.13"
  }
}
```

`recharts` не ставим: графиков в плане 1 нет, журнал показывает суммы числами.

- [ ] **Step 2: Конфигурация сборки**

`execution/frontend/vite.config.ts`:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 3000,
    proxy: { '/api': 'http://api:8000' },
  },
})
```

`execution/frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`execution/frontend/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

`execution/frontend/index.html`:

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Контент-сервис</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 3: Стили**

`execution/frontend/src/index.css` — копия файла `../nst-tg-monitor/frontend/src/index.css` без изменений (нейтральная палитра, ховер карточек, шапка таблицы, скроллбар, мобильные правила).

- [ ] **Step 4: Клиент API**

`execution/frontend/src/api.ts`:

```ts
import axios from 'axios'
import { message } from 'antd'

const api = axios.create({ baseURL: '/api' })

api.interceptors.response.use(
  r => r,
  error => {
    const status = error.response?.status
    if (status === 401 && !location.pathname.startsWith('/login')) {
      location.href = '/login'
    } else if (status === 403) {
      message.error('Нет доступа')
    } else {
      message.error(error.response?.data?.detail ?? 'Ошибка сервера')
    }
    return Promise.reject(error)
  },
)

export interface Profile { email: string; full_name: string; role: string }
export interface SiteBrief {
  id: number; name: string; domain: string; publish_target: string
  url_prefix: string; reference_images: number; is_ready: boolean
}
export interface SiteFull {
  id: number; name: string; domain: string; base_url: string
  api_token: string; is_active: boolean; publish_target: string
  site_description: string; tone_of_voice: string
  articles_parent_id: number | null; reference_article_id: number | null
  image_style_prompt: string; cover_mode: string; cover_style_prompt: string
  builder_template_html: string; builder_parent_id: number | null
  teaser_category_id: number | null; teaser_city_id: number | null
  teaser_location_id: number | null; watermark_path: string
  // Заполняются синхронизацией, в форме только читаются.
  articles_url_prefix: string; reference_images: number
  reference_synced_at: string | null
}
export interface ArticleRow {
  id: number; topic: string; title: string; status: string
  remote_url: string; error_text: string
}
export interface Batch {
  id: number; site_id: number; site_name: string; site_domain: string
  requested_count: number; status: string; error_text: string
  created_at: string; articles: ArticleRow[]
}
export interface Prompt { id: number; key: string; site_id: number | null; text: string }
export interface JobRow {
  id: number; kind: string; site_name: string; status: string; log_text: string
  cost: number; tokens_total: number; started_at: string; finished_at: string | null
}
export interface UserRow {
  id: number; email: string; full_name: string; role: string; is_active: boolean
}

export const login = (email: string, password: string) => {
  const form = new URLSearchParams({ username: email, password })
  return api.post<Profile>('/auth/login', form).then(r => r.data)
}
export const logout = () => api.post('/auth/logout')
export const me = () => api.get<Profile>('/auth/me').then(r => r.data)

export const getSites = () => api.get<SiteBrief[]>('/sites').then(r => r.data)
export const getAdminSites = () => api.get<SiteFull[]>('/admin/sites').then(r => r.data)
export const createSite = (d: Partial<SiteFull>) =>
  api.post<SiteFull>('/admin/sites', d).then(r => r.data)
export const updateSite = (id: number, d: Partial<SiteFull>) =>
  api.put<SiteFull>(`/admin/sites/${id}`, d).then(r => r.data)
export const deleteSite = (id: number) => api.delete(`/admin/sites/${id}`)
export const syncSite = (id: number) =>
  api.post<{ ok: boolean; url_prefix: string; pages: number
             reference_images: number; detail: string }>(`/admin/sites/${id}/sync`)
    .then(r => r.data)
export const uploadWatermark = (id: number, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return api.post(`/admin/sites/${id}/watermark`, form)
}

export const getBatches = () => api.get<Batch[]>('/article-batches').then(r => r.data)
export const getBatch = (id: number) =>
  api.get<Batch>(`/article-batches/${id}`).then(r => r.data)
export const createBatch = (site_id: number, count: number) =>
  api.post<Batch>('/article-batches', { site_id, count }).then(r => r.data)
export const saveTopics = (id: number, topics: string[]) =>
  api.put<Batch>(`/article-batches/${id}/topics`, { topics }).then(r => r.data)
export const runBatch = (id: number) =>
  api.post<Batch>(`/article-batches/${id}/run`).then(r => r.data)
export const retryArticle = (id: number) => api.post(`/articles/${id}/retry`)

export const getSettings = () => api.get<Record<string, string>>('/admin/settings').then(r => r.data)
export const updateSettings = (d: Record<string, string>) =>
  api.put<Record<string, string>>('/admin/settings', d).then(r => r.data)

export const getPrompts = () => api.get<Prompt[]>('/admin/prompts').then(r => r.data)
export const savePrompt = (d: Partial<Prompt>) =>
  api.put<Prompt>('/admin/prompts', d).then(r => r.data)
export const testPrompt = (text: string, variables: Record<string, unknown>) =>
  api.post<{ rendered: string; answer: string; tokens_total: number; cost: number }>(
    '/admin/prompts/test', { text, variables }).then(r => r.data)

export const getJobs = () => api.get<JobRow[]>('/jobs').then(r => r.data)

export const getUsers = () => api.get<UserRow[]>('/admin/users').then(r => r.data)
export const createUser = (d: Record<string, unknown>) =>
  api.post<UserRow>('/admin/users', d).then(r => r.data)
export const updateUser = (id: number, d: Record<string, unknown>) =>
  api.put<UserRow>(`/admin/users/${id}`, d).then(r => r.data)
export const deleteUser = (id: number) => api.delete(`/admin/users/${id}`)

export default api
```

- [ ] **Step 5: Контекст авторизации**

`execution/frontend/src/auth.tsx`:

```tsx
import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { Spin } from 'antd'
import { me, Profile } from './api'

interface AuthState {
  profile: Profile | null
  setProfile: (p: Profile | null) => void
  isAdmin: boolean
}

const AuthContext = createContext<AuthState>({
  profile: null, setProfile: () => {}, isAdmin: false,
})

export const useAuth = () => useContext(AuthContext)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Cookie httpOnly из JS не читается — единственный способ узнать, жива ли
    // сессия, это спросить бэкенд.
    me().then(setProfile).catch(() => setProfile(null)).finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <div style={{ padding: 80, textAlign: 'center' }}><Spin size="large" /></div>
  }

  return (
    <AuthContext.Provider value={{ profile, setProfile, isAdmin: profile?.role === 'admin' }}>
      {children}
    </AuthContext.Provider>
  )
}
```

- [ ] **Step 6: Точка входа и оболочка**

`execution/frontend/src/main.tsx`:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import App from './App'
import { AuthProvider } from './auth'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={ruRU}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ConfigProvider>
  </React.StrictMode>,
)
```

`execution/frontend/src/App.tsx` — layout и тема переносятся из
`../nst-tg-monitor/frontend/src/App.tsx`: акцент `#dca34c` и все производные оттенки
остаются без изменений, меняются только пункты меню, логотип и роуты, добавляется
фильтрация admin-пунктов и выход.

```tsx
import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom'
import { ConfigProvider, Layout, Menu, Drawer, Button, Dropdown } from 'antd'
import {
  FileTextOutlined, HomeOutlined, GlobalOutlined, BulbOutlined,
  SettingOutlined, TeamOutlined, HistoryOutlined, MenuOutlined,
  CloseOutlined, UserOutlined,
} from '@ant-design/icons'
import { useAuth } from './auth'
import { logout } from './api'
import LoginPage from './pages/LoginPage'
import ArticlesPage from './pages/ArticlesPage'
import BatchPage from './pages/BatchPage'
import JobsPage from './pages/JobsPage'
import AdminSitesPage from './pages/AdminSitesPage'
import AdminPromptsPage from './pages/AdminPromptsPage'
import AdminSettingsPage from './pages/AdminSettingsPage'
import AdminUsersPage from './pages/AdminUsersPage'

const { Sider, Content } = Layout

const navItems = [
  { key: '/articles', label: 'Статьи', icon: <FileTextOutlined />, admin: false },
  { key: '/builders', label: 'Строители', icon: <HomeOutlined />, admin: false },
  { key: '/jobs', label: 'Журнал', icon: <HistoryOutlined />, admin: false },
  { key: '/admin/sites', label: 'Сайты', icon: <GlobalOutlined />, admin: true },
  { key: '/admin/prompts', label: 'Промпты', icon: <BulbOutlined />, admin: true },
  { key: '/admin/settings', label: 'Настройки', icon: <SettingOutlined />, admin: true },
  { key: '/admin/users', label: 'Пользователи', icon: <TeamOutlined />, admin: true },
]

// Палитра перенесена из nst-tg-monitor без изменений: производные оттенки там
// подобраны вручную, а не сгенерированы из colorPrimary алгоритмом.
const antTheme = {
  token: {
    colorPrimary: '#dca34c',
    colorBgContainer: '#ffffff',
    colorBgLayout: '#f4f4f5',
    borderRadius: 8,
    borderRadiusLG: 12,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    colorTextHeading: '#18181b',
    colorText: '#3f3f46',
    colorTextSecondary: '#71717a',
    colorBorder: '#e4e4e7',
    colorBorderSecondary: '#f0f0f0',
    boxShadow: '0 1px 3px 0 rgb(0 0 0 / 0.06), 0 1px 2px -1px rgb(0 0 0 / 0.06)',
    boxShadowSecondary: '0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.07)',
  },
  components: {
    Layout: { siderBg: '#ffffff', bodyBg: '#f4f4f5' },
    Menu: {
      itemBg: 'transparent', itemSelectedBg: '#fef8ee', itemSelectedColor: '#dca34c',
      itemActiveBg: '#fef8ee', itemHoverBg: '#f4f4f5', itemHoverColor: '#18181b',
      itemColor: '#52525b', iconSize: 15,
    },
    Card: { borderRadius: 12 },
    Button: { borderRadius: 8 },
    Table: { headerBg: '#fafafa', borderRadius: 12 },
  },
}

const logoBlock = (
  <div style={{
    padding: '18px 16px 16px', display: 'flex', alignItems: 'center', gap: 10,
    borderBottom: '1px solid #f0f0f0', marginBottom: 8,
  }}>
    <div style={{
      width: 30, height: 30, borderRadius: 8,
      background: 'linear-gradient(135deg, #dca34c 0%, #e8b96a 100%)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: 14, fontWeight: 700, color: '#fff', flexShrink: 0, letterSpacing: '-0.5px',
    }}>K1</div>
    <div>
      <div style={{ fontWeight: 600, fontSize: 13, color: '#18181b', lineHeight: 1.2 }}>
        Контент-сервис
      </div>
      <div style={{ fontSize: 11, color: '#a1a1aa', lineHeight: 1.2 }}>
        Статьи и строители
      </div>
    </div>
  </div>
)

export default function App() {
  return (
    <ConfigProvider theme={antTheme}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<Shell />} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}

function Shell() {
  const { profile, isAdmin } = useAuth()
  const [isMobile, setIsMobile] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    setIsMobile(mq.matches)
    const handler = (e: MediaQueryListEvent) => {
      setIsMobile(e.matches)
      if (!e.matches) setDrawerOpen(false)
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  if (!profile) return <Navigate to="/login" replace />

  const items = navItems.filter(i => !i.admin || isAdmin)

  const userMenu = (
    <Dropdown menu={{
      items: [{
        key: 'logout', label: 'Выйти',
        onClick: async () => { await logout(); location.href = '/login' },
      }],
    }}>
      <Button type="text" icon={<UserOutlined />} style={{ color: '#52525b' }}>
        {profile.full_name}
      </Button>
    </Dropdown>
  )

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {!isMobile && (
        <Sider width={220} style={{
          background: '#fff', borderRight: '1px solid #e4e4e7',
          position: 'sticky', top: 0, height: '100vh', overflow: 'auto',
        }}>
          {logoBlock}
          <SideNav items={items} />
        </Sider>
      )}

      {isMobile && (
        <Drawer
          open={drawerOpen} onClose={() => setDrawerOpen(false)} placement="left" width={220}
          closeIcon={<CloseOutlined style={{ fontSize: 14, color: '#71717a' }} />}
          styles={{
            body: { padding: 0 },
            header: { padding: '14px 16px', borderBottom: '1px solid #f0f0f0', minHeight: 'auto' },
            mask: { background: 'rgb(0 0 0 / 0.35)' },
          }}
          title="Контент-сервис"
        >
          <div style={{ paddingTop: 8 }}>
            <SideNav items={items} onNavigate={() => setDrawerOpen(false)} />
          </div>
        </Drawer>
      )}

      <Layout style={{ background: '#f4f4f5' }}>
        <div style={{
          position: 'sticky', top: 0, zIndex: 100, background: '#fff',
          borderBottom: '1px solid #e4e4e7', padding: '0 16px', height: 52,
          display: 'flex', alignItems: 'center', gap: 12, justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {isMobile && (
              <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)}
                      style={{ color: '#52525b', width: 36, height: 36, padding: 0 }} />
            )}
            <span style={{ fontWeight: 600, fontSize: 14, color: '#18181b' }}>
              Контент-сервис
            </span>
          </div>
          {userMenu}
        </div>

        <Content style={{ padding: isMobile ? 16 : 28 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/articles" replace />} />
            <Route path="/articles" element={<ArticlesPage />} />
            <Route path="/articles/:id" element={<BatchPage />} />
            <Route path="/builders" element={
              <div style={{ color: '#71717a' }}>
                Раздел «Строители» появится в плане 2. Пока процесс идёт через CLI.
              </div>
            } />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/admin/sites" element={<AdminSitesPage />} />
            <Route path="/admin/prompts" element={<AdminPromptsPage />} />
            <Route path="/admin/settings" element={<AdminSettingsPage />} />
            <Route path="/admin/users" element={<AdminUsersPage />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

function SideNav({ items, onNavigate }: {
  items: typeof navItems; onNavigate?: () => void
}) {
  const { pathname } = useLocation()
  return (
    <Menu
      mode="inline"
      selectedKeys={[items.find(i => pathname.startsWith(i.key))?.key ?? pathname]}
      onClick={onNavigate}
      style={{ border: 'none', background: 'transparent', padding: '0 8px' }}
      items={items.map(item => ({
        key: item.key, icon: item.icon,
        label: <NavLink to={item.key} style={{ textDecoration: 'none' }}>{item.label}</NavLink>,
      }))}
    />
  )
}
```

- [ ] **Step 7: Проверить сборку**

Run: `cd execution && docker compose up -d api && docker compose run --rm frontend sh -c "npm install && npm run build"`
Expected: сборка падает на отсутствующих файлах страниц — это ожидаемо, страницы создаются в задачах 21–25. Отсутствие ошибок в `App.tsx`, `api.ts`, `auth.tsx` подтверждает корректность каркаса.

- [ ] **Step 8: Commit**

```bash
git add execution/frontend
git commit -m "feat: каркас фронтенда, тема nst-tg-monitor, клиент API"
```

---

### Task 21: Страница входа

**Files:**
- Create: `execution/frontend/src/pages/LoginPage.tsx`

- [ ] **Step 1: Реализация**

`execution/frontend/src/pages/LoginPage.tsx`:

```tsx
import { useState } from 'react'
import { Button, Card, Form, Input, Typography } from 'antd'
import { login } from '../api'
import { useAuth } from '../auth'

export default function LoginPage() {
  const { setProfile } = useAuth()
  const [loading, setLoading] = useState(false)

  const onFinish = async (values: { email: string; password: string }) => {
    setLoading(true)
    try {
      const profile = await login(values.email, values.password)
      setProfile(profile)
      location.href = '/articles'
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: '#f4f4f5', padding: 16,
    }}>
      <Card style={{ width: 360 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12, margin: '0 auto 12px',
            background: 'linear-gradient(135deg, #dca34c 0%, #e8b96a 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 18, fontWeight: 700, color: '#fff',
          }}>K1</div>
          <Typography.Title level={4} style={{ margin: 0 }}>Контент-сервис</Typography.Title>
        </div>
        <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item name="email" label="Email"
                     rules={[{ required: true, message: 'Введите email' }]}>
            <Input autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item name="password" label="Пароль"
                     rules={[{ required: true, message: 'Введите пароль' }]}>
            <Input.Password autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={loading}>
            Войти
          </Button>
        </Form>
      </Card>
    </div>
  )
}
```

- [ ] **Step 2: Ручная проверка**

Run: `cd execution && docker compose up api frontend`

Открой `http://localhost:3000/login`. Проверь:
1. вход под созданным в Task 4 админом ведёт на `/articles`;
2. неверный пароль показывает сообщение об ошибке и не пускает;
3. после входа в шапке видно имя, «Выйти» возвращает на `/login`.

- [ ] **Step 3: Commit**

```bash
git add execution/frontend/src/pages/LoginPage.tsx
git commit -m "feat: страница входа"
```

---

### Task 22: Список партий статей

**Files:**
- Create: `execution/frontend/src/pages/ArticlesPage.tsx`

- [ ] **Step 1: Реализация**

`execution/frontend/src/pages/ArticlesPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Button, Card, Form, InputNumber, Modal, Select, Space, Table, Tag, Typography,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { Batch, SiteBrief, createBatch, getBatches, getSites } from '../api'

const STATUS: Record<string, { color: string; label: string }> = {
  topics_pending: { color: 'processing', label: 'Подбираются темы' },
  topics_review: { color: 'warning', label: 'Темы на согласовании' },
  running: { color: 'processing', label: 'Генерируется' },
  done: { color: 'success', label: 'Готово' },
  failed: { color: 'error', label: 'Ошибка' },
}

export default function ArticlesPage() {
  const navigate = useNavigate()
  const [batches, setBatches] = useState<Batch[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => getBatches().then(setBatches)

  useEffect(() => {
    load()
    getSites().then(setSites)
  }, [])

  useEffect(() => {
    // Поллинг только пока есть незавершённые партии — иначе сервер опрашивается
    // впустую весь рабочий день.
    const active = batches.some(b => ['topics_pending', 'running'].includes(b.status))
    if (!active) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [batches])

  const submit = async (values: { site_id: number; count: number }) => {
    const batch = await createBatch(values.site_id, values.count)
    setOpen(false)
    form.resetFields()
    navigate(`/articles/${batch.id}`)
  }

  const selectedSite = sites.find(s => s.id === Form.useWatch('site_id', form))

  return (
    <>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Партии статей</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          Новая партия
        </Button>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id"
          dataSource={batches}
          pagination={{ pageSize: 20 }}
          onRow={r => ({ onClick: () => navigate(`/articles/${r.id}`),
                         style: { cursor: 'pointer' } })}
          columns={[
            { title: 'Сайт', dataIndex: 'site_name' },
            { title: 'Статей', dataIndex: 'requested_count', width: 90 },
            {
              title: 'Готово', width: 110,
              render: (_, r: Batch) =>
                `${r.articles.filter(a => a.status === 'published').length} / ${r.articles.length}`,
            },
            {
              title: 'Статус', dataIndex: 'status', width: 200,
              render: (s: string) => (
                <Tag color={STATUS[s]?.color}>{STATUS[s]?.label ?? s}</Tag>
              ),
            },
            {
              title: 'Создана', dataIndex: 'created_at', width: 160,
              render: (v: string) => dayjs(v).format('DD.MM.YYYY HH:mm'),
            },
          ]}
        />
      </Card>

      <Modal open={open} onCancel={() => setOpen(false)} onOk={form.submit}
             title="Новая партия статей" okText="Подобрать темы" destroyOnClose>
        <Form form={form} layout="vertical" onFinish={submit}
              initialValues={{ count: 5 }} requiredMark={false}>
          <Form.Item name="site_id" label="Сайт"
                     rules={[{ required: true, message: 'Выберите сайт' }]}>
            <Select
              placeholder="Выберите сайт"
              options={sites.map(s => ({ value: s.id, label: `${s.name} — ${s.domain}` }))}
            />
          </Form.Item>
          <Form.Item name="count" label="Сколько статей"
                     rules={[{ required: true, message: 'Укажите количество' }]}>
            <InputNumber min={1} max={50} style={{ width: '100%' }} />
          </Form.Item>
          {/* Домен и раздел показываются до запуска: при десятке сайтов промах —
              самая вероятная авария, а всё создаётся черновиком именно там. */}
          {selectedSite && selectedSite.is_ready && (
            <div style={{ color: '#71717a', fontSize: 13 }}>
              Черновики будут созданы на <b>{selectedSite.domain}</b> в разделе{' '}
              <b>{selectedSite.url_prefix}</b>, по {selectedSite.reference_images}{' '}
              картинки в статье (столько же, сколько в эталонной статье сайта).
            </div>
          )}
          {selectedSite && !selectedSite.is_ready && (
            <Alert
              type="warning" showIcon
              message="Сайт не готов к генерации"
              description="Эталонная статья не синхронизирована. Попроси администратора
                           нажать «Проверить и синхронизировать» на карточке сайта."
            />
          )}
        </Form>
      </Modal>
    </>
  )
}
```

- [ ] **Step 2: Ручная проверка**

Открой `http://localhost:3000/articles`. Проверь: кнопка «Новая партия» открывает форму,
выбор сайта показывает домен подсказкой, отправка создаёт партию и переводит на её экран.

- [ ] **Step 3: Commit**

```bash
git add execution/frontend/src/pages/ArticlesPage.tsx
git commit -m "feat: экран списка партий статей"
```

---

### Task 23: Экран партии — согласование тем и прогресс

**Files:**
- Create: `execution/frontend/src/pages/BatchPage.tsx`

- [ ] **Step 1: Реализация**

`execution/frontend/src/pages/BatchPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Alert, Button, Card, Input, Popconfirm, Space, Table, Tag, Typography, message,
} from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { ArticleRow, Batch, getBatch, retryArticle, runBatch, saveTopics } from '../api'

const EDITABLE = ['topics_pending', 'topics_review', 'failed']

const ARTICLE_STATUS: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: 'Ожидает' },
  generating: { color: 'processing', label: 'Генерируется' },
  generated: { color: 'processing', label: 'Собрана' },
  published: { color: 'success', label: 'Черновик на сайте' },
  failed: { color: 'error', label: 'Ошибка' },
}

export default function BatchPage() {
  const { id } = useParams()
  const batchId = Number(id)
  const [batch, setBatch] = useState<Batch | null>(null)
  const [topics, setTopics] = useState<string[]>([])
  const [saving, setSaving] = useState(false)

  const load = () => getBatch(batchId).then(b => {
    setBatch(b)
    if (EDITABLE.includes(b.status)) setTopics(b.articles.map(a => a.topic))
    return b
  })

  useEffect(() => { load() }, [batchId])

  useEffect(() => {
    if (!batch) return
    const active = batch.status === 'topics_pending' || batch.status === 'running'
      || batch.articles.some(a => a.status === 'generating')
    if (!active) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [batch])

  if (!batch) return null

  const editable = EDITABLE.includes(batch.status)

  const persist = async (next: string[]) => {
    setSaving(true)
    try {
      setBatch(await saveTopics(batchId, next))
      setTopics(next)
    } finally {
      setSaving(false)
    }
  }

  const start = async () => {
    if (topics.join('|') !== batch.articles.map(a => a.topic).join('|')) {
      await persist(topics)
    }
    await runBatch(batchId)
    message.success(`Запущено. Черновики появятся на ${batch.site_domain}`)
    load()
  }

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        Партия №{batch.id} — {batch.site_name}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Черновики создаются на <b>{batch.site_domain}</b>. Публикует их менеджер
        вручную в админке сайта.
      </Typography.Paragraph>

      {batch.error_text && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }}
               message="Не удалось подобрать темы" description={batch.error_text} />
      )}

      {batch.status === 'topics_pending' && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }}
               message="Подбираем темы — обычно занимает до минуты" />
      )}

      {editable ? (
        <Card title="Согласование тем" extra={
          <Space>
            <Button icon={<PlusOutlined />} onClick={() => setTopics([...topics, ''])}>
              Добавить тему
            </Button>
            <Button type="primary" loading={saving}
                    disabled={topics.filter(t => t.trim()).length === 0} onClick={start}>
              Запустить генерацию
            </Button>
          </Space>
        }>
          <Space direction="vertical" style={{ width: '100%' }}>
            {topics.map((topic, index) => (
              <Space.Compact key={index} style={{ width: '100%' }}>
                <Input
                  value={topic}
                  placeholder="Заголовок статьи"
                  onChange={e => {
                    const next = [...topics]
                    next[index] = e.target.value
                    setTopics(next)
                  }}
                />
                <Button icon={<DeleteOutlined />}
                        onClick={() => setTopics(topics.filter((_, i) => i !== index))} />
              </Space.Compact>
            ))}
            {topics.length === 0 && (
              <Typography.Text type="secondary">
                Тем нет — добавь свои или создай партию заново.
              </Typography.Text>
            )}
          </Space>
        </Card>
      ) : (
        <Card styles={{ body: { padding: 0 } }}>
          <Table
            rowKey="id"
            dataSource={batch.articles}
            pagination={false}
            columns={[
              { title: 'Тема', dataIndex: 'topic' },
              {
                title: 'Статус', dataIndex: 'status', width: 200,
                render: (s: string) => (
                  <Tag color={ARTICLE_STATUS[s]?.color}>{ARTICLE_STATUS[s]?.label ?? s}</Tag>
                ),
              },
              {
                title: 'Черновик', width: 140,
                render: (_, r: ArticleRow) => r.remote_url
                  ? <a href={r.remote_url} target="_blank" rel="noreferrer">открыть</a>
                  : '—',
              },
              {
                title: '', width: 60,
                render: (_, r: ArticleRow) => r.status === 'failed' ? (
                  <Popconfirm title="Повторить генерацию этой статьи?"
                              onConfirm={async () => { await retryArticle(r.id); load() }}>
                    <Button type="text" icon={<ReloadOutlined />} />
                  </Popconfirm>
                ) : null,
              },
            ]}
            expandable={{
              expandedRowRender: (r: ArticleRow) => (
                <Typography.Text type="danger">{r.error_text}</Typography.Text>
              ),
              rowExpandable: (r: ArticleRow) => Boolean(r.error_text),
            }}
          />
        </Card>
      )}
    </>
  )
}
```

- [ ] **Step 2: Ручная проверка**

Создай партию на 2 статьи. Проверь: темы появляются и редактируются, лишняя удаляется,
своя добавляется, «Запустить генерацию» переключает экран в таблицу прогресса, статусы
обновляются сами, ссылка на черновик открывает страницу на сайте, у упавшей статьи
раскрывается текст ошибки и работает кнопка повтора.

- [ ] **Step 3: Commit**

```bash
git add execution/frontend/src/pages/BatchPage.tsx
git commit -m "feat: экран партии — согласование тем и прогресс"
```

---

### Task 24: Админские экраны — сайты

**Files:**
- Create: `execution/frontend/src/pages/AdminSitesPage.tsx`

- [ ] **Step 1: Реализация**

`execution/frontend/src/pages/AdminSitesPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import {
  Button, Card, Form, Input, InputNumber, Modal, Select, Space, Table, Tag,
  Typography, Upload, message,
} from 'antd'
import { PlusOutlined, UploadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  SiteFull, createSite, deleteSite, getAdminSites, syncSite, updateSite, uploadWatermark,
} from '../api'

export default function AdminSitesPage() {
  const [sites, setSites] = useState<SiteFull[]>([])
  const [editing, setEditing] = useState<SiteFull | null>(null)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => getAdminSites().then(setSites)
  useEffect(() => { load() }, [])

  const openForm = (site: SiteFull | null) => {
    setEditing(site)
    form.resetFields()
    // Токен приходит маской — подставлять её в поле нельзя, иначе маска
    // уедет обратно на сервер как новое значение.
    form.setFieldsValue(site ? { ...site, api_token: '' } : {
      publish_target: 'pages', cover_mode: 'prompt', is_active: true,
    })
    setOpen(true)
  }

  const submit = async (values: Partial<SiteFull>) => {
    if (editing) await updateSite(editing.id, values)
    else await createSite(values)
    setOpen(false)
    load()
  }

  const sync = async (site: SiteFull) => {
    const result = await syncSite(site.id)
    if (result.ok) {
      message.success(`Раздел ${result.url_prefix}, статей в нём ${result.pages}, `
                      + `картинок в эталоне ${result.reference_images}`)
      load()
    } else {
      // Ошибка чужого сайта показывается целиком: администратору нужно понять,
      // что именно не так — токен, id раздела или id эталона.
      message.error(result.detail, 8)
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Сайты</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openForm(null)}>
          Добавить сайт
        </Button>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id" dataSource={sites} pagination={false}
          columns={[
            { title: 'Название', dataIndex: 'name' },
            { title: 'Домен', dataIndex: 'domain' },
            { title: 'Токен', dataIndex: 'api_token', width: 140 },
            {
              title: 'Раздел', width: 180,
              render: (_, r: SiteFull) => r.articles_url_prefix
                ? `${r.articles_url_prefix} (parent ${r.articles_parent_id ?? '—'})`
                : <Tag color="warning">не синхронизирован</Tag>,
            },
            {
              title: 'Эталон', width: 190,
              render: (_, r: SiteFull) => r.reference_synced_at
                ? `${r.reference_images} карт. · ${dayjs(r.reference_synced_at).format('DD.MM HH:mm')}`
                : '—',
            },
            {
              title: 'Знак', width: 90,
              render: (_, r: SiteFull) => r.watermark_path
                ? <Tag color="success">есть</Tag> : <Tag>нет</Tag>,
            },
            {
              title: '', width: 320,
              render: (_, r: SiteFull) => (
                <Space>
                  <Button size="small" onClick={() => sync(r)}>
                    Проверить и синхронизировать
                  </Button>
                  <Upload
                    showUploadList={false}
                    beforeUpload={async file => {
                      await uploadWatermark(r.id, file as File)
                      message.success('Водяной знак загружен')
                      load()
                      return false
                    }}
                  >
                    <Button size="small" icon={<UploadOutlined />}>Знак</Button>
                  </Upload>
                  <Button size="small" type="link" onClick={() => openForm(r)}>Правка</Button>
                  <Button size="small" type="link" danger
                          onClick={async () => { await deleteSite(r.id); load() }}>
                    Удалить
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal open={open} onCancel={() => setOpen(false)} onOk={form.submit} width={720}
             title={editing ? `Сайт ${editing.domain}` : 'Новый сайт'} destroyOnClose>
        <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="name" label="Название" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="domain" label="Домен" rules={[{ required: true }]}>
            <Input placeholder="stroybaza-samara.ru" />
          </Form.Item>
          <Form.Item name="base_url" label="Базовый URL" rules={[{ required: true }]}>
            <Input placeholder="https://stroybaza-samara.ru" />
          </Form.Item>
          <Form.Item name="api_token" label="Токен API"
                     extra={editing ? 'Пусто — оставить текущий токен' : undefined}
                     rules={[{ required: !editing, message: 'Токен обязателен' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="site_description" label="О чём сайт и для кого"
                     extra="Тематика и аудитория — по этому описанию подбираются темы
                            статей. Чем конкретнее, тем меньше промахов."
                     rules={[{ required: true, message: 'Без описания темы будут мимо' }]}>
            <Input.TextArea rows={3}
                            placeholder="Строительная база в Самаре: материалы для
                                         частных застройщиков, аудитория — люди,
                                         строящие дом своими силами или с подрядчиком" />
          </Form.Item>
          <Form.Item name="tone_of_voice" label="Тон материалов">
            <Input.TextArea rows={2}
                            placeholder="практичный, без рекламных обещаний,
                                         обращение на «вы»" />
          </Form.Item>
          <Form.Item name="publish_target" label="Куда публиковать">
            <Select options={[
              { value: 'pages', label: 'Страницы (staticpages)' },
              { value: 'articles', label: 'Раздел articles' },
            ]} />
          </Form.Item>
          {/* Префикс url не вводится: он берётся с самой родительской страницы
              при синхронизации, иначе рано или поздно разъедется с сайтом. */}
          <Form.Item name="articles_parent_id" label="ID родительской страницы раздела"
                     extra={editing?.articles_url_prefix
                       ? `Раздел на сайте: ${editing.articles_url_prefix}`
                       : 'Раздел определится при синхронизации'}
                     rules={[{ required: true, message: 'Без раздела публиковать некуда' }]}>
            <InputNumber style={{ width: '100%' }} placeholder="25" />
          </Form.Item>
          <Form.Item name="reference_article_id" label="ID эталонной статьи"
                     extra={editing?.reference_synced_at
                       ? `Синхронизирована ${dayjs(editing.reference_synced_at)
                            .format('DD.MM.YYYY HH:mm')}, картинок в ней:
                            ${editing.reference_images}`
                       : 'Её разметка — шаблон для всех статей сайта, а число картинок
                          в ней задаёт число картинок в новых статьях'}
                     rules={[{ required: true, message: 'Эталон обязателен' }]}>
            <InputNumber style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="image_style_prompt" label="Стиль контентных картинок">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="cover_mode" label="Обложка">
            <Select options={[
              { value: 'prompt', label: 'По своему промпту' },
              { value: 'like_existing', label: 'Как существующие обложки сайта' },
            ]} />
          </Form.Item>
          <Form.Item name="cover_style_prompt" label="Стиль обложки">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Typography.Text type="secondary">
            Ниже — карточки-тизеры каталога строителей (план 2), к обложкам статей
            отношения не имеют.
          </Typography.Text>
          <Space style={{ marginTop: 12 }}>
            <Form.Item name="teaser_category_id" label="category">
              <InputNumber />
            </Form.Item>
            <Form.Item name="teaser_city_id" label="city">
              <InputNumber />
            </Form.Item>
            <Form.Item name="teaser_location_id" label="location">
              <InputNumber />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </>
  )
}
```

- [ ] **Step 2: Завести четыре рабочих сайта**

Через форму заведи сайты с их реальными токенами из `.env` и значениями таксономии
из спеки §5: bolars.ru (3/1/1), bolars-shop.ru (4/1/1), vetonit-center.ru (3/1/1),
stroybaza-samara.ru (3/2/1, `articles_parent_id` = 25).

Для каждого заполни «О чём сайт и для кого» и «Тон материалов» — без них темы будут
подбираться мимо тематики. Укажи id эталонной статьи и нажми
«Проверить и синхронизировать».

Expected: сообщение вида «Раздел /blog/, статей в нём N, картинок в эталоне K». Для
stroybaza-samara.ru N > 0 и K > 0; в таблице появляется префикс раздела и время
синхронизации. Если раздел на сайте не `/blog/`, а другой — в сообщении будет именно
он: префикс берётся с родительской страницы, а не подставляется по умолчанию.

- [ ] **Step 3: Commit**

```bash
git add execution/frontend/src/pages/AdminSitesPage.tsx
git commit -m "feat: экран управления сайтами"
```

---

### Task 25: Админские экраны — промпты, настройки, пользователи, журнал

**Files:**
- Create: `execution/frontend/src/pages/AdminPromptsPage.tsx`
- Create: `execution/frontend/src/pages/AdminSettingsPage.tsx`
- Create: `execution/frontend/src/pages/AdminUsersPage.tsx`
- Create: `execution/frontend/src/pages/JobsPage.tsx`

- [ ] **Step 1: Экран промптов с тестовым прогоном**

`execution/frontend/src/pages/AdminPromptsPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import {
  Button, Card, Input, Select, Space, Tabs, Typography, message,
} from 'antd'
import { Prompt, SiteBrief, getPrompts, getSites, savePrompt, testPrompt } from '../api'

const KEYS = [
  { key: 'topics', label: 'Темы', vars: { count: 5, site_name: 'Стройбаза', site_description: 'Строительная база в Самаре, аудитория — частные застройщики', tone_of_voice: 'практичный, без рекламных обещаний', existing_titles: ['Чем утеплить дом'] } },
  { key: 'article_body', label: 'Текст статьи', vars: { topic: 'Чем утеплить каркасный дом', site_name: 'Стройбаза', site_description: 'Строительная база в Самаре, аудитория — частные застройщики', tone_of_voice: 'практичный, без рекламных обещаний', reference_html: '<article><p>образец</p><img></article>', image_count: 2, image_paths: ['/media/uploads/article-img/article_1-1.webp', '/media/uploads/article-img/article_1-2.webp'] } },
  { key: 'cover', label: 'Обложка', vars: { topic: 'Чем утеплить каркасный дом', cover_style: 'широкая обложка' } },
  { key: 'content_image', label: 'Картинка в тексте', vars: { topic: 'Чем утеплить каркасный дом', paragraph: 'иллюстрация 1 из 2', image_style: 'фото стройки' } },
]

export default function AdminPromptsPage() {
  const [prompts, setPrompts] = useState<Prompt[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [siteId, setSiteId] = useState<number | null>(null)
  const [texts, setTexts] = useState<Record<string, string>>({})
  const [result, setResult] = useState<{ rendered: string; answer: string; tokens_total: number; cost: number } | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => getPrompts().then(rows => {
    setPrompts(rows)
    const next: Record<string, string> = {}
    for (const item of KEYS) {
      const override = rows.find(r => r.key === item.key && r.site_id === siteId)
      const global = rows.find(r => r.key === item.key && r.site_id === null)
      next[item.key] = override?.text ?? global?.text ?? ''
    }
    setTexts(next)
  })

  useEffect(() => { getSites().then(setSites) }, [])
  useEffect(() => { load() }, [siteId])

  const save = async (key: string) => {
    await savePrompt({ key, site_id: siteId, text: texts[key] })
    message.success(siteId ? 'Промпт сохранён для сайта' : 'Глобальный промпт сохранён')
    load()
  }

  const runTest = async (key: string) => {
    setBusy(true)
    setResult(null)
    try {
      setResult(await testPrompt(texts[key], KEYS.find(k => k.key === key)!.vars))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Промпты</Typography.Title>
        <Select
          style={{ width: 320 }} value={siteId} onChange={setSiteId}
          options={[{ value: null, label: 'Глобальные значения по умолчанию' },
                    ...sites.map(s => ({ value: s.id, label: `Только для ${s.domain}` }))]}
        />
      </Space>

      <Tabs items={KEYS.map(item => ({
        key: item.key,
        label: item.label,
        children: (
          <Card>
            <Input.TextArea
              rows={16} value={texts[item.key] ?? ''}
              onChange={e => setTexts({ ...texts, [item.key]: e.target.value })}
            />
            <Space style={{ marginTop: 12 }}>
              <Button type="primary" onClick={() => save(item.key)}>Сохранить</Button>
              <Button loading={busy} onClick={() => runTest(item.key)}>Тест</Button>
              <Typography.Text type="secondary">
                Переменные: {Object.keys(item.vars).join(', ')}
              </Typography.Text>
            </Space>

            {result && (
              <div style={{ marginTop: 16 }}>
                <Typography.Title level={5}>Отрендеренный промпт</Typography.Title>
                <pre style={{ background: '#fafafa', padding: 12, borderRadius: 8,
                              whiteSpace: 'pre-wrap' }}>{result.rendered}</pre>
                <Typography.Title level={5}>Ответ модели</Typography.Title>
                <pre style={{ background: '#fafafa', padding: 12, borderRadius: 8,
                              whiteSpace: 'pre-wrap' }}>{result.answer}</pre>
                <Typography.Text type="secondary">
                  Токенов: {result.tokens_total} · стоимость: {result.cost}
                </Typography.Text>
              </div>
            )}
          </Card>
        ),
      }))} />
      {prompts.length === 0 && <Typography.Text type="secondary">Промпты загружаются…</Typography.Text>}
    </>
  )
}
```

- [ ] **Step 2: Экран настроек**

> Бэкенд (Task 6) отдаёт секретное поле `routerai_api_key` либо маской
> (`sk-...alue`), либо пустой строкой — никогда как значение, пригодное для
> повторной отправки. **Отправлять полученное от GET значение этого поля
> обратно в PUT нельзя**: пустая строка означает «не менять», а любая
> непустая строка будет зашифрована и сохранена как новый секрет —
> необратимо затерев прежний ключ. Форма ниже это уже учитывает: значение
> поля сбрасывается в `''` сразу после загрузки и после каждого сохранения
> (`routerai_api_key: ''`), а не копируется из ответа сервера — так и
> оставить, при рефакторинге этого экрана не убирать сброс. Если ответ GET
> содержит ключ `_errors` (словарь «имя настройки → текст ошибки»,
> появляется, например, при устаревшем `ENCRYPTION_KEY`) — это диагностика
> для админа, показать её предупреждением над формой; отправлять `_errors`
> обратно в PUT не нужно (роутер настроек и так игнорирует незнакомые ключи
> payload, но поле не должно попадать в тело запроса намеренно).

`execution/frontend/src/pages/AdminSettingsPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, Select, Typography, message } from 'antd'
import { getSettings, updateSettings } from '../api'

export default function AdminSettingsPage() {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  useEffect(() => { getSettings().then(v => form.setFieldsValue({ ...v, routerai_api_key: '' })) }, [])

  const submit = async (values: Record<string, string>) => {
    setLoading(true)
    try {
      const saved = await updateSettings(values)
      form.setFieldsValue({ ...saved, routerai_api_key: '' })
      message.success('Настройки сохранены')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Настройки RouterAI</Typography.Title>
      <Card style={{ maxWidth: 620 }}>
        <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="routerai_base_url" label="Базовый URL">
            <Input />
          </Form.Item>
          <Form.Item name="routerai_api_key" label="Ключ API"
                     extra="Пусто — оставить текущий ключ">
            <Input.Password placeholder="не отображается" />
          </Form.Item>
          <Form.Item name="text_model" label="Модель для текста">
            <Input />
          </Form.Item>
          <Form.Item name="image_model" label="Модель для картинок">
            <Input />
          </Form.Item>
          <Form.Item name="image_quality" label="Качество картинок"
                     extra="high дороже примерно втрое: ≈16.8 против ≈5.4 за кадр">
            <Select options={[{ value: 'medium', label: 'medium' },
                              { value: 'high', label: 'high' }]} />
          </Form.Item>
          <Form.Item name="image_size" label="Размер генерации">
            <Select options={[{ value: '1536x1024', label: '1536×1024' },
                              { value: '1024x1024', label: '1024×1024' },
                              { value: '1024x1536', label: '1024×1536' }]} />
          </Form.Item>
          <Form.Item name="image_workers" label="Параллельных генераций">
            <Input />
          </Form.Item>
          <Form.Item name="llm_max_retries" label="Повторов при сбое">
            <Input />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={loading}>Сохранить</Button>
        </Form>
      </Card>
    </>
  )
}
```

- [ ] **Step 3: Экран пользователей**

`execution/frontend/src/pages/AdminUsersPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import {
  Button, Card, Form, Input, Modal, Select, Space, Switch, Table, Tag, Typography,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { UserRow, createUser, deleteUser, getUsers, updateUser } from '../api'

export default function AdminUsersPage() {
  const [users, setUsers] = useState<UserRow[]>([])
  const [editing, setEditing] = useState<UserRow | null>(null)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => getUsers().then(setUsers)
  useEffect(() => { load() }, [])

  const openForm = (user: UserRow | null) => {
    setEditing(user)
    form.resetFields()
    form.setFieldsValue(user ? { ...user, password: '' }
                             : { role: 'manager', is_active: true })
    setOpen(true)
  }

  const submit = async (values: Record<string, unknown>) => {
    if (editing) await updateUser(editing.id, values)
    else await createUser(values)
    setOpen(false)
    load()
  }

  return (
    <>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Пользователи</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openForm(null)}>
          Добавить
        </Button>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id" dataSource={users} pagination={false}
          columns={[
            { title: 'Email', dataIndex: 'email' },
            { title: 'Имя', dataIndex: 'full_name' },
            {
              title: 'Роль', dataIndex: 'role', width: 140,
              render: (r: string) => (
                <Tag color={r === 'admin' ? 'gold' : 'default'}>
                  {r === 'admin' ? 'Администратор' : 'Менеджер'}
                </Tag>
              ),
            },
            {
              title: 'Активен', dataIndex: 'is_active', width: 100,
              render: (v: boolean) => v ? 'да' : 'нет',
            },
            {
              title: '', width: 160,
              render: (_, r: UserRow) => (
                <Space>
                  <Button size="small" type="link" onClick={() => openForm(r)}>Правка</Button>
                  <Button size="small" type="link" danger
                          onClick={async () => { await deleteUser(r.id); load() }}>
                    Удалить
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal open={open} onCancel={() => setOpen(false)} onOk={form.submit}
             title={editing ? 'Изменение пользователя' : 'Новый пользователь'} destroyOnClose>
        <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="email" label="Email" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="full_name" label="Имя" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="Роль">
            <Select options={[{ value: 'manager', label: 'Менеджер' },
                              { value: 'admin', label: 'Администратор' }]} />
          </Form.Item>
          <Form.Item name="password" label="Пароль"
                     extra={editing ? 'Пусто — оставить текущий' : 'Минимум 8 символов'}
                     rules={[{ required: !editing, message: 'Введите пароль' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="is_active" label="Активен" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
```

- [ ] **Step 4: Журнал задач**

`execution/frontend/src/pages/JobsPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Card, Space, Statistic, Table, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { JobRow, getJobs } from '../api'

const KIND: Record<string, string> = {
  generate_topics: 'Подбор тем',
  run_batch: 'Генерация партии',
  retry_article: 'Повтор статьи',
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobRow[]>([])

  useEffect(() => {
    getJobs().then(setJobs)
    const timer = setInterval(() => getJobs().then(setJobs), 10000)
    return () => clearInterval(timer)
  }, [])

  const running = jobs.filter(j => j.status === 'running').length
  const totalCost = jobs.reduce((sum, j) => sum + j.cost, 0)

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Журнал задач</Typography.Title>

      <Space style={{ marginBottom: 16 }} size={16}>
        <Card size="small" style={{ minWidth: 180 }}>
          <Statistic title="Выполняется сейчас" value={running} />
        </Card>
        <Card size="small" style={{ minWidth: 180 }}>
          <Statistic title="Расход RouterAI" value={totalCost} precision={1} />
        </Card>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id" dataSource={jobs} pagination={{ pageSize: 20 }}
          columns={[
            { title: 'Задача', dataIndex: 'kind', render: (k: string) => KIND[k] ?? k },
            { title: 'Сайт', dataIndex: 'site_name' },
            {
              title: 'Статус', dataIndex: 'status', width: 130,
              render: (s: string) => (
                <Tag color={s === 'ok' ? 'success' : s === 'failed' ? 'error' : 'processing'}>
                  {s === 'ok' ? 'готово' : s === 'failed' ? 'ошибка' : 'выполняется'}
                </Tag>
              ),
            },
            { title: 'Итог', dataIndex: 'log_text' },
            { title: 'Токены', dataIndex: 'tokens_total', width: 100 },
            {
              title: 'Стоимость', dataIndex: 'cost', width: 110,
              render: (v: number) => v.toFixed(1),
            },
            {
              title: 'Начата', dataIndex: 'started_at', width: 150,
              render: (v: string) => dayjs(v).format('DD.MM HH:mm:ss'),
            },
          ]}
        />
      </Card>
    </>
  )
}
```

- [ ] **Step 5: Собрать фронт целиком**

Run: `cd execution && docker compose run --rm frontend sh -c "npm install && npm run build"`
Expected: PASS — сборка проходит без ошибок TypeScript

- [ ] **Step 6: Сквозная ручная проверка**

Пройди путь целиком на реальном сайте: настройки RouterAI → промпт «Темы» с кнопкой
«Тест» → новая партия на 2 статьи для stroybaza-samara.ru → согласование тем → запуск →
черновики на сайте → журнал показывает стоимость.

**Это приёмка плана.** Открой созданный черновик и сравни его с эталонной статьей сайта:
структура, набор тегов и классов должны совпадать, картинки — открываться, водяной
знак — стоять на контентных картинках и отсутствовать на обложке. Расхождения
устраняются правкой промпта `article_body` на экране «Промпты», а не правкой кода.

- [ ] **Step 7: Commit**

```bash
git add execution/frontend/src/pages
git commit -m "feat: экраны промптов, настроек, пользователей и журнала"
```

---

## Фаза 6 — Деплой

### Task 26: Продовая сборка и выкладка на VPS

**Files:**
- Create: `execution/frontend/Dockerfile`, `execution/frontend/nginx.conf`
- Create: `execution/docker-compose.prod.yml`, `execution/.env.prod.example`
- Create: `DEPLOY.md`

- [ ] **Step 1: Образ фронтенда**

`execution/frontend/Dockerfile`:

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
```

`execution/frontend/nginx.conf`:

```nginx
# Ограничение частоты на логин. Собственной защиты от перебора у эндпоинта
# нет: единственный тормоз — bcrypt (~225 мс на попытку, то есть ~4 попытки
# в секунду). Вдобавок /login работает усилителем нагрузки — из-за постоянного
# dummy-хеша каждый запрос, верный или нет, занимает воркер на те же ~225 мс,
# поэтому несколько десятков одновременных POST забивают контейнер. Для панели
# на 2–3 человек 10 попыток в минуту с адреса — с большим запасом.
limit_req_zone $binary_remote_addr zone=login:10m rate=10r/m;

server {
    listen 80;
    client_max_body_size 20m;

    # Точное совпадение (`=`) имеет приоритет над префиксным `location /api/`
    # ниже, поэтому логин уходит сюда, а не в общий блок.
    location = /api/auth/login {
        limit_req zone=login burst=5 nodelay;
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /api/ {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 600s;   # генерация партии идёт долго
    }

    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;   # SPA-роутинг
    }
}
```

- [ ] **Step 2: Продовый compose**

`execution/docker-compose.prod.yml`:

```yaml
x-backend-base: &backend-base
  build: ./backend
  restart: unless-stopped
  volumes:
    - media:/app/media
  environment: &backend-env
    # Форма `:?` — обязательное значение: незаданная переменная в форме
    # `${VAR}` разворачивается в пустую строку, и прод молча поднимется,
    # подписывая токены пустым секретом. `:?` роняет запуск с внятным текстом.
    DATABASE_URL: postgresql+psycopg://app:${DB_PASSWORD:?DB_PASSWORD не задан}@postgres:5432/content
    REDIS_URL: redis://redis:6379/0
    JWT_SECRET: ${JWT_SECRET:?JWT_SECRET не задан}
    ENCRYPTION_KEY: ${ENCRYPTION_KEY:?ENCRYPTION_KEY не задан}
    COOKIE_SECURE: "true"
    MEDIA_DIR: /app/media
    TZ: Europe/Samara

services:
  migrate:
    <<: *backend-base
    restart: "no"
    command: alembic upgrade head
    depends_on:
      postgres:
        condition: service_healthy

  api:
    <<: *backend-base
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    depends_on:
      migrate:
        condition: service_completed_successfully

  worker:
    <<: *backend-base
    # concurrency=2: одновременно идут максимум две партии, внутри каждой
    # картинки уже генерируются в несколько потоков.
    command: celery -A app.celery_app worker --loglevel=info --concurrency=2
    depends_on:
      migrate:
        condition: service_completed_successfully

  frontend:
    build: ./frontend
    restart: unless-stopped
    ports: ["127.0.0.1:8080:80"]
    depends_on: [api]

  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: content
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${DB_PASSWORD:?DB_PASSWORD не задан}
    volumes: [postgres_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d content"]
      interval: 10s

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --appendonly yes
    volumes: [redis_data:/data]

volumes:
  postgres_data:
  redis_data:
  media:
```

`execution/.env.prod.example`:

```
DB_PASSWORD=
JWT_SECRET=
# Сгенерировать: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Смена этого ключа делает нечитаемыми все сохранённые токены сайтов и ключ RouterAI.
ENCRYPTION_KEY=
```

- [ ] **Step 3: DEPLOY.md**

Создай `DEPLOY.md` по образцу `../inntec-inbox/DEPLOY.md` со следующими разделами:
подготовка VPS (docker, docker compose), клонирование репозитория, заполнение
`.env.prod` (три секрета выше), первый запуск
(`docker compose -f docker-compose.prod.yml up -d --build`), создание первого
администратора (`docker compose -f docker-compose.prod.yml run --rm api python create_admin.py`),
внешний nginx с TLS на `127.0.0.1:8080`, обновление версии, бэкап
(`docker compose exec postgres pg_dump -U app content`), восстановление, типовые
неполадки (нерасшифровываемые токены после смены `ENCRYPTION_KEY`, worker без
задач, 403 от API сайта).

- [ ] **Step 4: Проверить продовую сборку локально**

Run:
```bash
cd execution && cp .env.prod.example .env.prod
# заполнить три значения
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
curl -s localhost:8080/api/health
```
Expected: `{"status":"ok"}`

- [ ] **Step 5: Выкатить на VPS и завести пользователей**

Разверни по `DEPLOY.md`, создай администратора, заведи 2–3 менеджеров, перенеси
карточки сайтов и промпты. Прогони одну партию из 2 статей на реальном сайте.

- [ ] **Step 6: Commit**

```bash
git add execution/frontend/Dockerfile execution/frontend/nginx.conf execution/docker-compose.prod.yml execution/.env.prod.example DEPLOY.md
git commit -m "feat: продовая сборка и инструкция по деплою"
```

---

## Что остаётся плану 2

Не входит в этот план и переносится в `orchestration/<дата>-plan2-builders.md`:

1. Миграция `execution/data/companies.db` (SQLite) в Postgres с добавлением `site_id`
   и статуса публикации — без неё дедуп строителей не будет знать, кто уже выложен.
2. Загрузка выгрузки Яндекс.Карт (xlsx) через UI и импорт с фильтрами регион/сфера
   (порт `step1_import_yandex.py`).
3. Скрейпинг сайтов компаний ради маркетингового текста (порт `step2_scrape_company.py`).
4. Переписывание текста через RouterAI и подстановка в `builder_template_html`
   (замена `step3_fill_template.py`).
5. Публикация страниц черновиками и создание карточек-тизеров в `addresses-services`
   с `category`/`city`/`location` из карточки сайта (порт `step5_*`, `step6_manage_teasers.py`).
6. Раздел «Строители» во фронтенде вместо текущей заглушки.

CLI-скрипты `execution/step*.py` до сдачи плана 2 остаются рабочими и не трогаются.

## Самопроверка плана

Сверка с `directions/2026-08-04-content-service-design.md`:

| Раздел спеки | Задачи |
|---|---|
| §3 Архитектура и стек | 1, 2, 26 |
| §4 Роли и доступ | 3, 4, 19 |
| §5 Модель данных | 2, 5, 9, 12, 14 |
| §6 Настройки и секреты | 5, 6, 25 |
| §7 Промпты | 12, 13, 25 |
| §5 «Синхронизация эталона» | 11 (модуль `sites/reference.py` + эндпоинт `sync`), 16 (эталон из кеша, число картинок по нему), 24 (кнопка и время синхронизации) |
| §8 Процесс «Статьи» | 15, 16, 17, 18, 22, 23 |
| §9 Процесс «Строители» | вынесен в план 2, поля модели заведены в задаче 9 |
| §10 Поверхность API | 4, 6, 11, 13, 18, 19 |
| §11 Структура фронтенда | 20–25 |
| §12 Задачи, журнал, стоимость | 14, 17, 18, 25 |
| §13 Тестирование | тесты в каждой задаче фазы 0–4 |
| §14 Деплой | 26 |
| §15 Риски | 1 → 13 и 25 (промпты + приёмка), 2 → 6 и 25 (качество и журнал), 3 → 22 и 23 (домен и раздел на экране), 4 → 11 (sync), 5 → 5 (SecretDecryptionError), 6 → 11 и 24 (время синхронизации видно, обновление в кнопку) |

Открытых мест нет: каждый шаг содержит либо готовый код, либо точный путь к файлу-образцу
в `inntec-inbox`/`nst-tg-monitor` с перечнем изменений.

