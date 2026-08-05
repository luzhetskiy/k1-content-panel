from sqlalchemy import text

from app.models.article import ArticleBatch


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


def test_email_uppercase_duplicate_rejected(admin_client, manager):
    # Зеркалит login()/create_admin.py: email хранится и сравнивается через
    # .lower(), иначе "Manager@k1.ru" завёлся бы вторым пользователем рядом
    # с "manager@k1.ru" (unique=True в БД их различает — разные строки),
    # и войти под новым адресом можно было бы только тем написанием,
    # каким его завели, что удивило бы того, кто наберёт email иначе.
    resp = admin_client.post("/api/admin/users", json={
        "email": "MANAGER@k1.ru", "full_name": "Дубль", "role": "manager",
        "password": "password123"})
    assert resp.status_code == 400


def test_short_password_rejected(admin_client):
    resp = admin_client.post("/api/admin/users", json={
        "email": "x@k1.ru", "full_name": "X", "role": "manager", "password": "123"})
    assert resp.status_code == 422


def test_unknown_role_rejected(admin_client):
    resp = admin_client.post("/api/admin/users", json={
        "email": "x@k1.ru", "full_name": "X", "role": "superadmin",
        "password": "password123"})
    assert resp.status_code == 400


def test_empty_password_on_update_keeps_current(admin_client, manager, client):
    admin_client.put(f"/api/admin/users/{manager.id}", json={
        "email": "manager@k1.ru", "full_name": "Переименован", "role": "manager",
        "password": "", "is_active": True})
    resp = client.post("/api/auth/login",
                       data={"username": "manager@k1.ru", "password": "managerpass"})
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Переименован"


def test_update_rejects_email_taken_by_another_user(admin_client, admin, manager):
    # Проверка занятости email в update_user идёт ДО присваивания: без неё
    # смена почты менеджера на уже занятую упала бы IntegrityError на
    # уникальном индексе users.email — то есть 500 вместо внятного 400.
    resp = admin_client.put(f"/api/admin/users/{manager.id}", json={
        "email": "admin@k1.ru", "full_name": "Менеджер", "role": "manager",
        "password": "", "is_active": True})
    assert resp.status_code == 400


def test_last_admin_cannot_be_deleted(admin_client, admin):
    resp = admin_client.delete(f"/api/admin/users/{admin.id}")
    assert resp.status_code == 400
    assert "последн" in resp.json()["detail"]


def test_last_admin_cannot_be_demoted(admin_client, admin):
    resp = admin_client.put(f"/api/admin/users/{admin.id}", json={
        "email": "admin@k1.ru", "full_name": "Админ", "role": "manager",
        "password": "", "is_active": True})
    assert resp.status_code == 400


def test_last_admin_cannot_be_deactivated(admin_client, admin):
    # Тот же замок, что и на демоцию роли (test_last_admin_cannot_be_demoted),
    # но через is_active=False, а не через role — losing_admin в
    # update_user проверяет оба пути одним условием, эта ветка отдельно
    # ничем не проверялась.
    resp = admin_client.put(f"/api/admin/users/{admin.id}", json={
        "email": "admin@k1.ru", "full_name": "Админ", "role": "admin",
        "password": "", "is_active": False})
    assert resp.status_code == 400


def test_second_admin_can_be_demoted(admin_client, admin, db_session):
    # Контрпроверка к test_last_admin_cannot_be_demoted: замок должен
    # срабатывать именно на ПОСЛЕДНЕМ активном админе, а не на роли "admin"
    # как таковой — второго активного админа демоутить можно.
    from app.api.security import hash_password
    from app.models.user import User

    second = User(email="second@k1.ru", full_name="Второй",
                  password_hash=hash_password("secondpass"), role="admin", is_active=True)
    db_session.add(second)
    db_session.commit()

    resp = admin_client.put(f"/api/admin/users/{second.id}", json={
        "email": "second@k1.ru", "full_name": "Второй", "role": "manager",
        "password": "", "is_active": True})
    assert resp.status_code == 200


def test_manager_can_be_deleted(admin_client, manager):
    assert admin_client.delete(f"/api/admin/users/{manager.id}").status_code == 200


def test_deleting_user_with_batch_sets_created_by_id_null(admin_client, manager, db_session):
    """Обязательная находка (см. orchestration-план, Task 19): черновичный
    `test_manager_can_be_deleted` удаляет менеджера, у которого нет ни одной
    ArticleBatch/JobRun, — это не ловит реальный дефект. До фикса (миграция
    450fdec97dd5_created_by_id_set_null_on_delete.py) внешний ключ
    article_batches.created_by_id -> users.id был объявлен без ON DELETE,
    Postgres применял NO ACTION по умолчанию, и удаление ЛЮБОГО пользователя,
    хоть раз создавшего партию статей, падало необработанным IntegrityError
    → 500. Проверено вручную на живом Postgres (docker compose up -d
    postgres, миграции до головы, backend/verify_fk_tmp.py, файл удалён
    после проверки):

        psycopg.errors.ForeignKeyViolation: update or delete on table
        "users" violates foreign key constraint
        "article_batches_created_by_id_fkey" on table "article_batches"

    Этот тест создаёт именно такую партию перед удалением и проверяет не
    только код ответа, но и то, что created_by_id партии стал NULL —
    поведение ON DELETE SET NULL, а не просто «запрос не упал».

    SQLite в тестах по умолчанию не применяет внешние ключи (проверено:
    `sqlite3.connect(':memory:').execute('PRAGMA foreign_keys').fetchone()`
    даёт `(0,)`), поэтому без явного включения ниже этот тест прошёл бы
    даже без фикса — регрессию поймал бы только ручной прогон на Postgres.
    Включаем PRAGMA только здесь, а не глобально в db_session
    (tests/conftest.py): глобальное включение проверено и ломает 14
    существующих тестов в test_models_article.py и test_ai_prompts.py,
    которые намеренно заводят ArticleBatch/Article/PromptTemplate с
    фиктивными site_id/created_by_id без настоящих родительских строк —
    это их осознанный стиль модельных юнит-тестов, трогать 14 файлов ради
    одного нового теста не в рамках Task 19. Включение работает, даже
    будучи выполненным после того, как фикстуры admin/manager уже
    закоммитили данные: PRAGMA foreign_keys нельзя менять внутри открытой
    транзакции, но между commit фикстуры и началом тела теста открытой
    транзакции нет (проверено отдельно на sqlite3-соединении).
    """
    db_session.execute(text("PRAGMA foreign_keys=ON"))

    batch = ArticleBatch(site_id=None, requested_count=1, created_by_id=manager.id)
    db_session.add(batch)
    db_session.commit()

    resp = admin_client.delete(f"/api/admin/users/{manager.id}")
    assert resp.status_code == 200

    db_session.refresh(batch)
    assert batch.created_by_id is None
