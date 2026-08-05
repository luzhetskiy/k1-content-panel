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
    # Email нормализуется на входе (не в колонке БД — она осталась
    # регистрозависимой, миграция ради этого сейчас избыточна): без этого
    # "Admin@K1.ru" и "admin@k1.ru" — разные пользователи для БД, и админ,
    # набравший почту в регистре, который подставил его собственный почтовый
    # клиент, не может войти. Самовосстановления при этом нет — только shell
    # в контейнер. См. также create_admin.py и Task 19 (создание/правка
    # пользователя должны нормализовать email тем же способом).
    email = form.username.strip().lower()
    user = db.scalars(select(User).where(User.email == email)).first()
    password_hash = user.password_hash if user and user.is_active else _DUMMY_HASH
    password_ok = verify_password(form.password, password_hash)
    if user is None or not user.is_active or not password_ok:
        raise HTTPException(401, "неверный email или пароль")

    token = create_access_token(user.id, user.role, secret=config.jwt_secret)
    response.set_cookie(
        "access_token", token,
        # samesite="strict", а не "lax": lax — site-scoped, не origin-scoped,
        # и приложение на соседнем поддомене того же домена (а в разработке —
        # что угодно на другом порту localhost) всё ещё считается same-site и
        # получит cookie в cross-origin запросах. Панель — SPA без входящих
        # внешних ссылок, поэтому strict здесь бесплатен: первая межсайтовая
        # навигация cookie не получит, а все запросы SPA после загрузки идут
        # с её собственного origin и остаются same-site.
        httponly=True, samesite="strict", secure=config.cookie_secure,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return UserProfile(email=user.email, full_name=user.full_name, role=user.role)


@router.post("/logout")
def logout(response: Response):
    # Атрибуты зеркалят set_cookie в login(): идентичность cookie для браузера
    # — это имя+домен+путь, и delete_cookie сработал бы и без httponly/
    # samesite/secure, но асимметрия между установкой и удалением одной и той
    # же cookie — лишний повод для будущей путаницы при правке одного места
    # без другого.
    response.delete_cookie(
        "access_token",
        httponly=True, samesite="strict", secure=config.cookie_secure,
    )
    return {"ok": True}


@router.get("/me", response_model=UserProfile)
def me(user: User = Depends(get_current_user)):
    return UserProfile(email=user.email, full_name=user.full_name, role=user.role)
