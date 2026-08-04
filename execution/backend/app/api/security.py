"""Пароли и JWT. Bcrypt используется напрямую (без passlib) — единственная
схема хеширования, лишняя абстракция не нужна.
"""

import bcrypt
from jose import jwt

from app.clock import utcnow

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 12 * 60


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: int, role: str, secret: str) -> str:
    expire = utcnow().timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = {"user_id": user_id, "role": role, "exp": expire}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=[ALGORITHM])
