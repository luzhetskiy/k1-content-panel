from app.models.user import User


def test_user_defaults():
    user = User(email="a@b.ru", full_name="Иван", password_hash="x")
    assert user.role is None or user.role == "manager"
    assert User.__tablename__ == "users"


def test_role_column_allows_admin():
    user = User(email="a@b.ru", full_name="Иван", password_hash="x", role="admin")
    assert user.role == "admin"
