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

    token = create_access_token(user.id, user.role, secret=config.jwt_secret)
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
