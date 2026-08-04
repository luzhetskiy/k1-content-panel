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
