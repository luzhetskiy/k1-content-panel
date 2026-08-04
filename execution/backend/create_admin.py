"""Разовое создание первого администратора: через UI его создать нельзя
(курица и яйцо — все админские эндпоинты требуют роли admin).

Запуск: docker compose run --rm backend python create_admin.py
"""

import getpass
import sys

from sqlalchemy import select

from app.api.security import hash_password
from app.db import SessionLocal
from app.models.user import User


def main() -> None:
    email = input("Email: ").strip()
    full_name = input("Имя: ").strip()
    password = getpass.getpass("Пароль: ")
    if getpass.getpass("Пароль ещё раз: ") != password:
        print("Пароли не совпадают")
        sys.exit(1)
    if len(password) < 8:
        print("Пароль короче 8 символов")
        sys.exit(1)
    try:
        password_hash = hash_password(password)
    except ValueError as e:
        # hash_password бросает ValueError на пароле длиннее 72 байт (bcrypt
        # молча обрезал бы его иначе, см. app/api/security.py) — превращаем
        # в понятное сообщение, а не даём упасть трейсбеком.
        print(str(e))
        sys.exit(1)

    db = SessionLocal()
    try:
        if db.scalars(select(User).where(User.email == email)).first():
            print(f"Пользователь {email} уже существует")
            sys.exit(1)
        db.add(User(email=email, full_name=full_name,
                    password_hash=password_hash, role="admin", is_active=True))
        db.commit()
        print(f"Администратор {email} создан")
    finally:
        db.close()


if __name__ == "__main__":
    main()
