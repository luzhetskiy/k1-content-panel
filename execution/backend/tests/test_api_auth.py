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
