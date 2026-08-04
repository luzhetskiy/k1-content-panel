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
