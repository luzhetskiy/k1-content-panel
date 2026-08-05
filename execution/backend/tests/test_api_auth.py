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
