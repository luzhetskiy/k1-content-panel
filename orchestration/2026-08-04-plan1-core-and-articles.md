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


def test_defaults_are_dev_friendly():
    cfg = Config()
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

- [x] **Step 5: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose build backend && docker compose run --rm --no-deps backend pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [x] **Step 6: Реализация config.py**

`execution/backend/app/__init__.py` — пустой файл.

`execution/backend/app/config.py`:

```python
from pydantic_settings import BaseSettings


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

    class Config:
        env_file = ".env"


config = Config()
```

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
  environment: &backend-env
    DATABASE_URL: postgresql+psycopg://app:app@postgres:5432/content
    REDIS_URL: redis://redis:6379/0
    JWT_SECRET: ${JWT_SECRET:-dev-jwt-secret-change-in-prod}
    ENCRYPTION_KEY: ${ENCRYPTION_KEY:-8Bq3mA0kXqL2pR7vT1yZ4nC6wE9sU5hJ0dF2gK8lM3o=}
    MEDIA_DIR: /app/media
    TZ: Europe/Samara

services:
  backend:
    <<: *backend-base
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
    ports: ["8000:8000"]
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
    ports: ["3000:3000"]
    depends_on:
      - api

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: content
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${DB_PASSWORD:-app}
    ports: ["5432:5432"]
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
- Create: `execution/backend/app/models/__init__.py`
- Create: `execution/backend/app/models/user.py`
- Test: `execution/backend/tests/test_models_user.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_models_user.py`:

```python
from app.models.user import User


def test_user_defaults():
    user = User(email="a@b.ru", full_name="Иван", password_hash="x")
    assert user.role is None or user.role == "manager"
    assert User.__tablename__ == "users"


def test_role_column_allows_admin():
    user = User(email="a@b.ru", full_name="Иван", password_hash="x", role="admin")
    assert user.role == "admin"
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_user.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Модель User**

`execution/backend/app/models/__init__.py` — пустой файл.

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

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_user.py -v`
Expected: PASS — 2 passed

- [ ] **Step 5: Инициализировать Alembic**

Run: `cd execution && docker compose run --rm --no-deps backend alembic init alembic`

Затем в `execution/backend/alembic.ini` заменить строку `sqlalchemy.url = ...` на пустую:

```ini
sqlalchemy.url =
```

URL берётся из окружения в `env.py` — держать пароль БД в файле конфигурации незачем.

- [ ] **Step 6: Настроить alembic/env.py**

Заменить содержимое `execution/backend/alembic/env.py` на:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import config as app_config
from app.db import Base
from app.models import user, setting, site, prompt_template, article, job  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", app_config.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

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

Импорт всех модулей моделей одной строкой обязателен: `autogenerate` видит только те
таблицы, чьи классы к моменту запуска зарегистрированы в `Base.metadata`. Модули
`setting`, `site`, `prompt_template`, `article`, `job` появятся в задачах 5, 9, 12, 14 —
до тех пор строку импорта держи закомментированной по одному имени, раскомментируя по
мере создания файлов. Начни с:

```python
from app.models import user  # noqa: F401
```

- [ ] **Step 7: Сгенерировать миграцию**

Run:
```bash
cd execution && docker compose up -d postgres && sleep 5
docker compose run --rm backend alembic revision --autogenerate -m "users"
docker compose run --rm backend alembic upgrade head
```
Expected: создан файл в `alembic/versions/`, вывод `Running upgrade -> <hash>, users`

- [ ] **Step 8: Commit**

```bash
git add execution/backend/alembic execution/backend/alembic.ini execution/backend/app/models
git commit -m "feat: alembic и таблица users"
```

---

### Task 3: Пароли и JWT

**Files:**
- Create: `execution/backend/app/api/__init__.py`
- Create: `execution/backend/app/api/security.py`
- Test: `execution/backend/tests/test_api_security.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_api_security.py`:

```python
import pytest

from app.api.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_is_not_plaintext():
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert hashed.startswith("$2b$")


def test_verify_accepts_correct_password():
    assert verify_password("secret123", hash_password("secret123")) is True


def test_verify_rejects_wrong_password():
    assert verify_password("wrong", hash_password("secret123")) is False


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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.security'`

- [ ] **Step 3: Реализация**

`execution/backend/app/api/__init__.py` — пустой файл.

`execution/backend/app/api/security.py`:

```python
"""Пароли и JWT. Bcrypt используется напрямую (без passlib) — единственная
схема хеширования, лишняя абстракция не нужна.
"""

import bcrypt
from jose import jwt

from app.clock import utcnow

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 12 * 60


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: int, role: str, secret: str) -> str:
    expire = utcnow().timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = {"user_id": user_id, "role": role, "exp": expire}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=[ALGORITHM])
```

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_security.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

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
- Create: `execution/backend/tests/conftest.py`
- Test: `execution/backend/tests/test_api_auth.py`

- [ ] **Step 1: Фикстуры для тестов API**

`execution/backend/tests/conftest.py`:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.api.security import hash_password
from app.db import Base
from app.main import app
from app.models.user import User

# SQLite в памяти: тесты API проверяют HTTP-контракт и роли, а не диалект БД.
# Postgres-специфичного SQL в моделях нет.
TEST_URL = "sqlite:///:memory:"


@pytest.fixture
def db_session():
    engine = create_engine(TEST_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
    # Подмена снимается здесь, а не в клиентских фикстурах: клиентов на тест
    # бывает несколько, и каждый снимал бы её из-под остальных.
    app.dependency_overrides.clear()


def _client_for(db_session, email: str = "", password: str = "") -> TestClient:
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    if email:
        client.post("/api/auth/login", data={"username": email, "password": password})
    return client


@pytest.fixture
def client(db_session):
    with _client_for(db_session) as c:
        yield c


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
def admin_client(db_session, admin):
    """Свой TestClient на роль. Общий клиент не годится: admin_client и
    manager_client встречаются в одном тесте, и второй логин затирал бы cookie
    первого — админские запросы молча уходили бы от имени менеджера и падали
    с 403 в тесте, который проверяет совсем другое."""
    with _client_for(db_session, "admin@k1.ru", "adminpass") as c:
        yield c


@pytest.fixture
def manager_client(db_session, manager):
    with _client_for(db_session, "manager@k1.ru", "managerpass") as c:
        yield c
```

- [ ] **Step 2: Написать падающий тест**

`execution/backend/tests/test_api_auth.py`:

```python
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
```

- [ ] **Step 3: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 4: Зависимости**

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

- [ ] **Step 5: Роутер авторизации**

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
    user = db.scalars(select(User).where(User.email == form.username)).first()
    password_hash = user.password_hash if user and user.is_active else _DUMMY_HASH
    password_ok = verify_password(form.password, password_hash)
    if user is None or not user.is_active or not password_ok:
        raise HTTPException(401, "неверный email или пароль")

    token = create_access_token(user.id, user.role, config.jwt_secret)
    response.set_cookie(
        "access_token", token,
        httponly=True, samesite="lax", secure=config.cookie_secure,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return UserProfile(email=user.email, full_name=user.full_name, role=user.role)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


@router.get("/me", response_model=UserProfile)
def me(user: User = Depends(get_current_user)):
    return UserProfile(email=user.email, full_name=user.full_name, role=user.role)
```

- [ ] **Step 6: Сборка приложения**

`execution/backend/app/main.py`:

```python
from fastapi import FastAPI

from app.api import auth

app = FastAPI(title="k1 content service")

app.include_router(auth.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 7: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_auth.py -v`
Expected: PASS — 6 passed

- [ ] **Step 8: Скрипт первого администратора**

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
    email = input("Email: ").strip()
    full_name = input("Имя: ").strip()
    password = getpass.getpass("Пароль: ")
    if getpass.getpass("Пароль ещё раз: ") != password:
        print("Пароли не совпадают")
        sys.exit(1)
    if len(password) < 8:
        print("Пароль короче 8 символов")
        sys.exit(1)

    db = SessionLocal()
    try:
        if db.scalars(select(User).where(User.email == email)).first():
            print(f"Пользователь {email} уже существует")
            sys.exit(1)
        db.add(User(email=email, full_name=full_name,
                    password_hash=hash_password(password), role="admin", is_active=True))
        db.commit()
        print(f"Администратор {email} создан")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Проверить скрипт вживую**

Run:
```bash
cd execution && docker compose up -d postgres && docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python create_admin.py
```
Expected: после ввода данных — `Администратор <email> создан`

- [ ] **Step 10: Commit**

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
- Test: `execution/backend/tests/test_settings.py`

- [ ] **Step 1: Написать падающий тест**

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


def test_mask_hides_middle():
    assert mask("abcdefghijklmnop") == "abc...mnop"


def test_mask_short_value_is_fully_hidden():
    """У 8-символьного значения «три первых плюс четыре последних» скрывает
    ровно один символ — маскировать так нельзя."""
    assert mask("short") == "***"


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


def test_service_defaults(db_session):
    service = SettingsService(db_session, KEY)
    assert service.get_str("absent", "default") == "default"
    assert service.get_int("absent", 4) == 4
    assert service.get_bool("absent", True) is True
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.settings'`

- [ ] **Step 3: crypto.py**

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


def encrypt(value: str, key: str) -> str:
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def decrypt(value: str, key: str) -> str:
    try:
        return Fernet(key.encode()).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError("значение зашифровано другим ключом") from exc


def mask(value: str) -> str:
    """Для отдачи в API: узнаваемо, но бесполезно для злоупотребления."""
    if len(value) < MIN_MASKABLE_LEN:
        return "***"
    return f"{value[:3]}...{value[-4:]}"
```

- [ ] **Step 4: Модель Setting**

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

- [ ] **Step 5: SettingsService**

`execution/backend/app/settings/service.py`:

```python
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

    def _raw(self, name: str) -> str | None:
        row = self.db.get(Setting, name)
        return row.value if row else None

    def set(self, name: str, value: str) -> None:
        row = self.db.get(Setting, name) or Setting(key=name)
        row.value = value
        row.is_secret = False
        self.db.merge(row)
        self.db.commit()

    def set_secret(self, name: str, value: str) -> None:
        row = self.db.get(Setting, name) or Setting(key=name)
        row.value = encrypt(value, self.key)
        row.is_secret = True
        self.db.merge(row)
        self.db.commit()

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
        raw = self._raw(name)
        return raw if raw is not None else default

    def get_int(self, name: str, default: int) -> int:
        raw = self._raw(name)
        return int(raw) if raw is not None else default

    def get_bool(self, name: str, default: bool) -> bool:
        raw = self._raw(name)
        return raw.lower() in ("1", "true", "yes") if raw is not None else default
```

- [ ] **Step 6: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_settings.py -v`
Expected: PASS — 9 passed

- [ ] **Step 7: Миграция**

Раскомментируй `setting` в списке импортов `alembic/env.py`, затем:

Run:
```bash
cd execution && docker compose run --rm backend alembic revision --autogenerate -m "settings"
docker compose run --rm backend alembic upgrade head
```
Expected: `Running upgrade <prev> -> <hash>, settings`

- [ ] **Step 8: Commit**

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

- [ ] **Step 1: Написать падающий тест**

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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_settings.py -v`
Expected: FAIL — 404 на `/api/admin/settings`

- [ ] **Step 3: Дефолты**

`execution/backend/app/seed.py`:

```python
"""Дефолтные значения настроек и промптов. Идемпотентна: существующие
записи не перезаписываются — отредактированный в админке промпт переживает
перезапуск сервиса."""

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


def seed_settings(db: Session) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value, is_secret=False))
    db.commit()
```

- [ ] **Step 4: Роутер настроек**

`execution/backend/app/api/admin_settings.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.config import config
from app.models.user import User
from app.seed import DEFAULT_SETTINGS, SECRET_KEYS, seed_settings
from app.settings.crypto import SecretDecryptionError, mask
from app.settings.service import SettingsService

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


def _service(db: Session) -> SettingsService:
    return SettingsService(db, config.encryption_key)


@router.get("")
def read_settings(db: Session = Depends(get_db),
                  _user: User = Depends(require_role("admin"))) -> dict:
    seed_settings(db)
    service = _service(db)
    result = {key: service.get_str(key, default) for key, default in DEFAULT_SETTINGS.items()}
    for key in SECRET_KEYS:
        try:
            value = service.get_secret(key)
        except SecretDecryptionError as exc:
            result[key] = f"ОШИБКА: {exc}"
            continue
        result[key] = mask(value) if value else ""
    return result


@router.put("")
def update_settings(payload: dict, db: Session = Depends(get_db),
                    _user: User = Depends(require_role("admin"))) -> dict:
    service = _service(db)
    for key, value in payload.items():
        if key in SECRET_KEYS:
            # Пустая строка = «не менять»: фронт получает маску, а не значение,
            # и не может отправить секрет обратно неизменным.
            if value:
                service.set_secret(key, str(value))
        elif key in DEFAULT_SETTINGS:
            service.set(key, str(value))
    return read_settings(db, _user)
```

- [ ] **Step 5: Подключить роутер**

В `execution/backend/app/main.py` заменить блок импорта и подключения на:

```python
from app.api import admin_settings, auth

app.include_router(auth.router)
app.include_router(admin_settings.router)
```

- [ ] **Step 6: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_settings.py -v`
Expected: PASS — 5 passed

- [ ] **Step 7: Commit**

```bash
git add execution/backend/app/seed.py execution/backend/app/api/admin_settings.py execution/backend/app/main.py execution/backend/tests/test_api_admin_settings.py
git commit -m "feat: настройки RouterAI с маскированием секретов"
```

---

### Task 7: Текстовый клиент RouterAI

**Files:**
- Create: `execution/backend/app/ai/__init__.py`
- Create: `execution/backend/app/ai/text.py`
- Test: `execution/backend/tests/test_ai_text.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_ai_text.py`:

```python
from types import SimpleNamespace

import pytest

from app.ai.text import LLMError, TextClient


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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai'`

- [ ] **Step 3: Реализация**

`execution/backend/app/ai/__init__.py` — пустой файл.

`execution/backend/app/ai/text.py`:

```python
"""Текстовая часть RouterAI. Провайдер OpenAI-совместимый, поэтому смена
провайдера — это смена base_url и модели в настройках, без правки кода.
"""

import json
import re
import time
from dataclasses import dataclass

from openai import OpenAI

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


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
    return OpenAI(base_url=base_url, api_key=api_key)


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
        raw = _FENCE.sub("", self._content(response)).strip()
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
            except Exception as exc:
                # Повторять имеет смысл только сбои транспорта. Разбор ответа
                # вынесен наружу, чтобы не ретраить осмысленный отказ модели.
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

    @staticmethod
    def _usage(response) -> tuple[int, int, float]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0, 0.0
        return (
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            float(getattr(usage, "cost", 0.0) or 0.0),
        )
```

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_text.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

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

- [ ] **Step 1: Написать падающий тест на кроп и упаковку**

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
```

- [ ] **Step 2: Написать падающий тест на водяной знак**

`execution/backend/tests/test_ai_watermark.py`:

```python
import io

from PIL import Image

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


def test_watermark_changes_bottom_right_corner():
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 255), mode="RGBA")
    before = Image.open(io.BytesIO(base)).convert("RGB")
    after = Image.open(io.BytesIO(apply_watermark(base, mark))).convert("RGB")
    corner = (1600 - 60, 900 - 60)
    assert before.getpixel(corner) != after.getpixel(corner)


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

- [ ] **Step 3: Запустить тесты, убедиться что падают**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_images.py tests/test_ai_watermark.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai.images'`

- [ ] **Step 4: Реализация images.py**

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
TIMEOUT = 420


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
        image = image.resize((MAX_WIDTH, round(image.height * MAX_WIDTH / image.width)),
                             Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=WEBP_QUALITY, method=6)
    return buffer.getvalue(), image.size


class ImageGenerator:
    def __init__(self, base_url: str, api_key: str, model: str, max_retries: int = 3,
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
                    body = response.json()
                    raw = base64.b64decode(body["data"][0]["b64_json"])
                    data, image_size = to_webp(raw, crop)
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

- [ ] **Step 5: Реализация watermark.py**

`execution/backend/app/ai/watermark.py`:

```python
"""Наложение водяного знака сайта на контентные картинки.

Знак ставится в правый нижний угол с отступом; на обложку статьи знак НЕ
накладывается — это витрина, а не иллюстрация внутри текста.
"""

from __future__ import annotations

import io

from PIL import Image

MARK_WIDTH_FRACTION = 0.22   # доля ширины кадра, которую занимает знак
MARGIN_FRACTION = 0.025      # отступ от краёв, доля ширины кадра
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

- [ ] **Step 6: Запустить тесты, убедиться что проходят**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_images.py tests/test_ai_watermark.py -v`
Expected: PASS — 12 passed

- [ ] **Step 7: Commit**

```bash
git add execution/backend/app/ai execution/backend/tests/test_ai_images.py execution/backend/tests/test_ai_watermark.py
git commit -m "feat: генерация картинок RouterAI, кроп и водяной знак"
```

---

## Фаза 2 — Сайты

### Task 9: Модель сайта

**Files:**
- Create: `execution/backend/app/models/site.py`
- Test: `execution/backend/tests/test_models_site.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_models_site.py`:

```python
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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_site.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.site'`

- [ ] **Step 3: Реализация**

`execution/backend/app/models/site.py`:

```python
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
```

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_site.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Миграция**

Раскомментируй `site` в импортах `alembic/env.py`, затем:

Run:
```bash
cd execution && docker compose run --rm backend alembic revision --autogenerate -m "sites"
docker compose run --rm backend alembic upgrade head
```
Expected: `Running upgrade <prev> -> <hash>, sites`

- [ ] **Step 6: Commit**

```bash
git add execution/backend/app/models/site.py execution/backend/alembic execution/backend/tests/test_models_site.py
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

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_sites_client.py`:

```python
import pytest

from app.sites.client import SiteAPIError, SiteClient, slugify


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {}
        self.text = text

    def json(self):
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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_sites_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sites'`

- [ ] **Step 3: Реализация**

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
    pass


def slugify(text: str, limit: int = 60) -> str:
    result = "".join(_TRANSLIT.get(c, c) for c in text.lower())
    result = re.sub(r"[^a-z0-9]+", "-", result)
    return result.strip("-")[:limit].strip("-")


def strip_html_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


class SiteClient:
    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}", "Accept": "application/json"}

    def _check(self, response, what: str):
        if not response.ok:
            raise SiteAPIError(f"{what}: HTTP {response.status_code}: {response.text[:300]}")
        return response

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
            body = response.json()
            pages += [item for item in body.get("results", [])
                      if (item.get("url") or "").startswith(url_prefix)]
            if not body.get("next"):
                return pages
            page_number += 1

    def get_page(self, page_id: int) -> dict:
        return self._check(
            requests.get(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                         headers=self._headers, timeout=self.timeout),
            f"страница {page_id}").json()

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
        return self._check(
            requests.post(f"{self.base_url}{STATICPAGES_PATH}", json=payload,
                          headers={**self._headers, "Content-Type": "application/json"},
                          timeout=self.timeout),
            "создание страницы").json()

    def set_page_cover(self, page_id: int, image_bytes: bytes, filename: str) -> str:
        """teaser_image — ImageField страницы: путём-строкой не задаётся (400),
        только multipart прямо в поле."""
        ctype = mimetypes.guess_type(filename)[0] or "image/webp"
        response = self._check(
            requests.patch(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                           headers=self._headers,
                           files={"teaser_image": (filename, io.BytesIO(image_bytes), ctype)},
                           timeout=120),
            "загрузка обложки")
        return response.json().get("teaser_image", "")

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
                          timeout=120),
            "загрузка файла")
        return f"/media/{upload_to}{filename}"
```

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_sites_client.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/sites execution/backend/tests/test_sites_client.py
git commit -m "feat: клиент API целевого сайта"
```

---

### Task 11: Синхронизация эталона и API сайтов

**Files:**
- Create: `execution/backend/app/sites/reference.py`
- Create: `execution/backend/app/api/sites.py`
- Create: `execution/backend/app/api/admin_sites.py`
- Modify: `execution/backend/app/main.py`
- Test: `execution/backend/tests/test_sites_reference.py`
- Test: `execution/backend/tests/test_api_sites.py`

- [ ] **Step 1: Написать падающий тест на синхронизацию**

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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_sites_reference.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sites.reference'`

- [ ] **Step 3: Реализация синхронизации**

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


def sync_site_reference(db: Session, site: Site, client) -> None:
    """Тянет раздел и эталон, заполняет кеш карточки. Бросает ReferenceError
    с человеческим текстом — вызывающий показывает его администратору."""
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
    db.commit()
```

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_sites_reference.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Написать падающий тест на API сайтов**

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
```

- [ ] **Step 6: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_sites.py -v`
Expected: FAIL — 404 на `/api/admin/sites`

- [ ] **Step 7: Публичный список сайтов**

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

- [ ] **Step 8: Админский роутер сайтов**

`execution/backend/app/api/admin_sites.py`:

```python
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
    if db.scalars(select(Site).where(Site.domain == payload.domain)).first():
        raise HTTPException(400, f"сайт {payload.domain} уже заведён")
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
    """
    site = _get_or_404(db, site_id)
    try:
        client = open_client(db, site)
        sync_site_reference(db, site, client)
        pages = client.list_section_pages(site.articles_url_prefix)
    except (SiteAPIError, ReferenceError, SecretDecryptionError) as exc:
        return SyncResult(ok=False, detail=str(exc))
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

- [ ] **Step 9: Подключить роутеры**

В `execution/backend/app/main.py` заменить импорт и подключение на:

```python
from app.api import admin_settings, admin_sites, auth, sites

app.include_router(auth.router)
app.include_router(admin_settings.router)
app.include_router(sites.router)
app.include_router(admin_sites.router)
```

- [ ] **Step 10: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_sites.py tests/test_sites_reference.py -v`
Expected: PASS — 23 passed

- [ ] **Step 11: Commit**

```bash
git add execution/backend/app/api execution/backend/app/sites/reference.py execution/backend/tests/test_api_sites.py execution/backend/tests/test_sites_reference.py
git commit -m "feat: API сайтов, синхронизация раздела и эталонной статьи"
```

---

## Фаза 3 — Промпты

### Task 12: Шаблоны промптов и их разрешение

**Files:**
- Create: `execution/backend/app/models/prompt_template.py`
- Create: `execution/backend/app/ai/prompts.py`
- Modify: `execution/backend/app/seed.py`
- Test: `execution/backend/tests/test_ai_prompts.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_ai_prompts.py`:

```python
import pytest

from app.ai.prompts import PROMPT_KEYS, PromptError, render_prompt, resolve_prompt
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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_prompts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.prompt_template'`

- [ ] **Step 3: Модель**

`execution/backend/app/models/prompt_template.py`:

```python
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
```

- [ ] **Step 4: Разрешение и рендер**

`execution/backend/app/ai/prompts.py`:

```python
"""Разрешение промпта (сайт → глобальный дефолт) и безопасный рендер Jinja2."""

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompt_template import PromptTemplate

PROMPT_KEYS = ("topics", "article_body", "cover", "content_image")

_env = SandboxedEnvironment(autoescape=False, trim_blocks=False, lstrip_blocks=False)


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


def render_prompt(template_text: str, variables: dict) -> str:
    try:
        return _env.from_string(template_text).render(**variables)
    except TemplateError as exc:
        # Промпты редактируются через админку, поэтому ошибка шаблона — обычная
        # пользовательская ошибка, а не сбой сервиса: её надо показать текстом.
        raise PromptError(f"ошибка шаблона (синтаксис или доступ): {exc}") from exc
```

- [ ] **Step 5: Дефолтные промпты**

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


def seed_prompts(db: Session) -> None:
    """Идемпотентна: отредактированный в админке промпт не перезаписывается."""
    existing = {
        row.key for row in
        db.query(PromptTemplate).filter(PromptTemplate.site_id.is_(None)).all()
    }
    for key in PROMPT_KEYS:
        if key not in existing:
            db.add(PromptTemplate(key=key, site_id=None, text=DEFAULT_PROMPTS[key]))
    db.commit()
```

- [ ] **Step 6: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_ai_prompts.py -v`
Expected: PASS — 9 passed

- [ ] **Step 7: Миграция**

Раскомментируй `prompt_template` в импортах `alembic/env.py`, затем:

Run:
```bash
cd execution && docker compose run --rm backend alembic revision --autogenerate -m "prompt_templates"
docker compose run --rm backend alembic upgrade head
```
Expected: `Running upgrade <prev> -> <hash>, prompt_templates`

- [ ] **Step 8: Commit**

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
- Test: `execution/backend/tests/test_api_admin_prompts.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_api_admin_prompts.py`:

```python
from types import SimpleNamespace

import pytest

from app.ai.text import TextResult


@pytest.fixture
def seeded(db_session):
    from app.seed import seed_prompts

    seed_prompts(db_session)


def test_manager_cannot_read_prompts(manager_client, seeded):
    assert manager_client.get("/api/admin/prompts").status_code == 403


def test_admin_lists_global_prompts(admin_client, seeded):
    body = admin_client.get("/api/admin/prompts").json()
    keys = {item["key"] for item in body if item["site_id"] is None}
    assert keys == {"topics", "article_body", "cover", "content_image"}


def test_admin_saves_site_override(admin_client, seeded, db_session):
    from app.models.site import Site
    from app.ai.prompts import resolve_prompt

    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e")
    db_session.add(site)
    db_session.commit()

    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": site.id, "text": "свой промпт"})
    assert resp.status_code == 200
    assert resolve_prompt(db_session, "topics", site.id) == "свой промпт"


def test_test_endpoint_returns_rendered_prompt_and_answer(admin_client, seeded, monkeypatch):
    monkeypatch.setattr(
        "app.api.admin_prompts.build_text_client",
        lambda db: SimpleNamespace(
            complete_text=lambda prompt: TextResult("ответ модели", 10, 20, 0.3)))

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


def test_test_endpoint_requires_admin(manager_client, seeded):
    resp = manager_client.post("/api/admin/prompts/test",
                               json={"text": "привет", "variables": {}})
    assert resp.status_code == 403
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_prompts.py -v`
Expected: FAIL — 404 на `/api/admin/prompts`

- [ ] **Step 3: Фабрика клиентов**

`execution/backend/app/ai/factory.py`:

```python
"""Сборка клиентов RouterAI из настроек в БД. Одна точка — чтобы задачи Celery
и API-эндпоинты не читали настройки каждый по-своему."""

from sqlalchemy.orm import Session

from app.ai.images import ImageGenerator
from app.ai.text import TextClient, build_client
from app.config import config
from app.settings.service import SettingsService


def _service(db: Session) -> SettingsService:
    return SettingsService(db, config.encryption_key)


def build_text_client(db: Session) -> TextClient:
    service = _service(db)
    client = build_client(
        service.get_str("routerai_base_url", "https://routerai.ru/api/v1"),
        service.get_secret("routerai_api_key"),
    )
    return TextClient(
        client,
        model=service.get_str("text_model", "anthropic/claude-sonnet-4-6"),
        max_retries=service.get_int("llm_max_retries", 3),
        backoff=2.0,
    )


def build_image_generator(db: Session) -> ImageGenerator:
    service = _service(db)
    return ImageGenerator(
        base_url=service.get_str("routerai_base_url", "https://routerai.ru/api/v1"),
        api_key=service.get_secret("routerai_api_key"),
        model=service.get_str("image_model", "openai/gpt-image-2"),
        max_retries=service.get_int("llm_max_retries", 3),
    )


def image_params(db: Session) -> dict:
    service = _service(db)
    return {
        "size": service.get_str("image_size", "1536x1024"),
        "quality": service.get_str("image_quality", "medium"),
        "workers": service.get_int("image_workers", 4),
    }
```

- [ ] **Step 4: Роутер промптов**

`execution/backend/app/api/admin_prompts.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.factory import build_text_client
from app.ai.prompts import PROMPT_KEYS, PromptError, render_prompt
from app.ai.text import LLMError
from app.api.deps import get_db, require_role
from app.models.prompt_template import PromptTemplate
from app.models.user import User
from app.seed import seed_prompts

router = APIRouter(prefix="/api/admin/prompts", tags=["admin-prompts"])


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
    variables: dict = {}


class PromptTestOut(BaseModel):
    rendered: str
    answer: str
    tokens_total: int
    cost: float


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
    if payload.key not in PROMPT_KEYS:
        raise HTTPException(400, f"неизвестный ключ промпта: {payload.key}")
    row = db.scalars(
        select(PromptTemplate).where(PromptTemplate.key == payload.key,
                                     PromptTemplate.site_id.is_(payload.site_id)
                                     if payload.site_id is None
                                     else PromptTemplate.site_id == payload.site_id)).first()
    if row is None:
        row = PromptTemplate(key=payload.key, site_id=payload.site_id)
        db.add(row)
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
        raise HTTPException(400, str(exc))

    try:
        result = build_text_client(db).complete_text(rendered)
    except LLMError as exc:
        raise HTTPException(502, f"RouterAI: {exc}")

    return PromptTestOut(
        rendered=rendered, answer=result.text,
        tokens_total=result.tokens_prompt + result.tokens_completion,
        cost=result.cost,
    )
```

- [ ] **Step 5: Подключить роутер**

В `execution/backend/app/main.py`:

```python
from app.api import admin_prompts, admin_settings, admin_sites, auth, sites

app.include_router(auth.router)
app.include_router(admin_settings.router)
app.include_router(sites.router)
app.include_router(admin_sites.router)
app.include_router(admin_prompts.router)
```

- [ ] **Step 6: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_api_admin_prompts.py -v`
Expected: PASS — 6 passed

- [ ] **Step 7: Commit**

```bash
git add execution/backend/app/ai/factory.py execution/backend/app/api/admin_prompts.py execution/backend/app/main.py execution/backend/tests/test_api_admin_prompts.py
git commit -m "feat: API промптов с тестовым прогоном"
```

---

## Фаза 4 — Статьи

### Task 14: Модели статей и журнала задач

**Files:**
- Create: `execution/backend/app/models/article.py`
- Create: `execution/backend/app/models/job.py`
- Test: `execution/backend/tests/test_models_article.py`

- [ ] **Step 1: Написать падающий тест**

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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_article.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.article'`

- [ ] **Step 3: Модели статей**

`execution/backend/app/models/article.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.db import Base


class ArticleBatch(Base):
    """Партия статей: в её рамках согласуется список тем.

    Статусы: topics_pending → topics_review → running → done | failed
    """

    __tablename__ = "article_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
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

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("article_batches.id", ondelete="CASCADE"))
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
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

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))       # cover | content
    position: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str] = mapped_column(Text, default="")
    remote_path: Mapped[str] = mapped_column(String(500), default="")
    cost: Mapped[float] = mapped_column(default=0.0)

    article: Mapped["Article"] = relationship(back_populates="images")
```

- [ ] **Step 4: Модели журнала**

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
    """Журнал фоновых задач: кто, что, когда и чем кончилось."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))       # generate_topics | build_article
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
    расход надо видеть до того, как он станет сюрпризом."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))       # text | image
    model: Mapped[str] = mapped_column(String(100), default="")
    tokens_prompt: Mapped[int] = mapped_column(Integer, default=0)
    tokens_completion: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["JobRun"] = relationship(back_populates="usage")
```

- [ ] **Step 5: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_models_article.py -v`
Expected: PASS — 6 passed

- [ ] **Step 6: Миграция**

Раскомментируй `article` и `job` в импортах `alembic/env.py`, затем:

Run:
```bash
cd execution && docker compose run --rm backend alembic revision --autogenerate -m "articles and jobs"
docker compose run --rm backend alembic upgrade head
```
Expected: `Running upgrade <prev> -> <hash>, articles and jobs`

- [ ] **Step 7: Commit**

```bash
git add execution/backend/app/models execution/backend/alembic execution/backend/tests/test_models_article.py
git commit -m "feat: модели статей, партий и журнала задач"
```

---

### Task 15: Генерация тем и дедуп

**Files:**
- Create: `execution/backend/app/articles/__init__.py`
- Create: `execution/backend/app/articles/topics.py`
- Test: `execution/backend/tests/test_articles_topics.py`

- [ ] **Step 1: Написать падающий тест**

`execution/backend/tests/test_articles_topics.py`:

```python
from app.articles.topics import filter_duplicates, normalize


def test_normalize_lowercases_and_drops_punctuation():
    assert normalize("Как выбрать Фундамент!") == "kak vybrat fundament"


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
    kept, dropped = filter_duplicates(["Тема A", "Тема B"], [])
    assert len(kept) == 2
    assert dropped == []
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_articles_topics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.articles'`

- [ ] **Step 3: Реализация**

`execution/backend/app/articles/__init__.py` — пустой файл.

`execution/backend/app/articles/topics.py`:

```python
"""Отбор тем: нормализация заголовков и отсев дублей.

Модель уже получает список существующих заголовков в промпте, но полагаться
только на неё нельзя — при десятках статей на сайте она начинает повторяться.
Локальный фильтр даёт детерминированную гарантию.
"""

from __future__ import annotations

import re

from app.sites.client import slugify

# Порог пересечения значимых слов, при котором темы считаются одинаковыми.
OVERLAP_THRESHOLD = 0.6

_STOPWORDS = {
    "kak", "chem", "chto", "gde", "kogda", "pochemu", "zachem", "kakoy", "kakaya",
    "kakie", "i", "v", "na", "s", "so", "dlya", "iz", "po", "pri", "ili", "a", "no",
    "li", "ne", "svoimi", "rukami", "zimoy", "letom", "vesnoy", "osenyu",
}


def normalize(title: str) -> str:
    """Латиница, без пунктуации, без стоп-слов — форма для сравнения тем."""
    words = slugify(title, limit=500).split("-")
    return " ".join(w for w in words if w and w not in _STOPWORDS)


def _tokens(title: str) -> set[str]:
    return set(normalize(title).split())


def _is_duplicate(candidate: set[str], known: list[set[str]]) -> bool:
    if not candidate:
        return True
    for other in known:
        if not other:
            continue
        overlap = len(candidate & other) / min(len(candidate), len(other))
        if overlap >= OVERLAP_THRESHOLD:
            return True
    return False


def filter_duplicates(proposed: list[str],
                      existing_titles: list[str]) -> tuple[list[str], list[str]]:
    """Возвращает (принятые, отсеянные). Дубли ищутся и среди существующих
    статей сайта, и внутри самого предложенного списка."""
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

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd execution && docker compose run --rm --no-deps backend pytest tests/test_articles_topics.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/articles execution/backend/tests/test_articles_topics.py
git commit -m "feat: отсев дублей тем статей"
```

---

### Task 16: Сборка одной статьи

Ядро процесса: текст → картинки → водяной знак → загрузка → страница-черновик → обложка.

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
```

- [ ] **Step 4: Задачи**

`execution/backend/app/tasks.py`:

```python
"""Фоновые задачи. Каждая обёрнута парой sync-функций: сама задача открывает
сессию, а логика живёт в `*_sync(db, ...)` — так её можно тестировать без брокера.
"""

from __future__ import annotations

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
    except (LLMError, PromptError, SiteAPIError, SecretDecryptionError) as exc:
        batch.status = "failed"
        batch.error_text = str(exc)
        db.commit()
        _finish_job(db, job, "failed", str(exc))


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

    for article in batch.articles:
        if article.status == "published":
            continue
        # Падение одной статьи не должно отменять остальные: билдер сам пишет
        # причину в error_text и оставляет статью в failed.
        build_for(db, article, site, site_client, job.id)
        db.commit()

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
    build_for(db, article, site, open_site_client(db, site), job.id)
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
    monkeypatch.setattr("app.api.article_batches.run_batch.delay",
                        lambda batch_id: sent.append(("run", batch_id)) or
                        type("R", (), {"id": "task-2"})())
    return sent


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
    run_batch.delay(batch.id)
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
    if db.scalars(select(User).where(User.email == payload.email)).first():
        raise HTTPException(400, f"пользователь {payload.email} уже существует")

    user = User(email=payload.email, full_name=payload.full_name, role=payload.role,
                is_active=payload.is_active,
                password_hash=hash_password(payload.password))
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

    user.email = payload.email
    user.full_name = payload.full_name
    user.role = payload.role
    user.is_active = payload.is_active
    if payload.password:
        if len(payload.password) < 8:
            raise HTTPException(422, "пароль короче 8 символов")
        user.password_hash = hash_password(payload.password)
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
server {
    listen 80;
    client_max_body_size 20m;

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
    DATABASE_URL: postgresql+psycopg://app:${DB_PASSWORD}@postgres:5432/content
    REDIS_URL: redis://redis:6379/0
    JWT_SECRET: ${JWT_SECRET}
    ENCRYPTION_KEY: ${ENCRYPTION_KEY}
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
      POSTGRES_PASSWORD: ${DB_PASSWORD}
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

