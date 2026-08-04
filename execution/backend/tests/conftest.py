import contextlib

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
import app.models  # noqa: F401 — регистрирует все модели в Base.metadata

from app.api.deps import get_db, require_role
from app.api.security import hash_password
from app.main import app
from app.models.user import User

# SQLite в памяти: модельные и (позже) API-тесты проверяют поведение, а не
# диалект БД. Postgres-специфичного SQL в моделях нет.
TEST_URL = "sqlite:///:memory:"


@pytest.fixture
def db_session():
    # poolclass=StaticPool — ОТСТУПЛЕНИЕ от дословного текста плана Task 4
    # (который явно требует "db_session не трогаем"), внесено в Task 4 при
    # реализации API-тестов. Без него FastAPI выполняет синхронные
    # эндпоинты в отдельном потоке (run_in_threadpool), а sqlite3 для
    # ":memory:" по умолчанию даёт КАЖДОМУ потоку свою, независимую базу —
    # эндпоинт видит пустую БД без таблиц ("no such table: users"), хотя
    # фикстура создала их и записала admin/manager в потоке теста.
    # StaticPool — задокументированный официальный паттерн FastAPI/SQLAlchemy
    # для тестирования with in-memory SQLite через TestClient. См. отчёт по
    # Task 4: без этой правки 4 из 6 тестов test_api_auth.py падают/ошибаются.
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
