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
