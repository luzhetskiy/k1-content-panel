"""Пароли и JWT. Bcrypt используется напрямую (без passlib) — единственная
схема хеширования, лишняя абстракция не нужна.

Отзыв токенов: деактивация пользователя и смена роли действуют немедленно —
`get_current_user`/`require_role` (Task 4) проверяют пользователя в БД на
каждый запрос. Смена пароля живые сессии НЕ убивает — уже выданный токен
остаётся рабочим до истечения `ACCESS_TOKEN_EXPIRE_MINUTES` (до 12 часов).
Для внутренней панели на 2–3 человека это осознанный компромисс, а не
недосмотр: полноценный отзыв (блэклист, `jti`) не реализуем.

Зависимость: `python-jose` не поддерживается с 2021 года и триггерит
`DeprecationWarning` на Python 3.12 (использует `datetime.utcnow()` внутри
`jwt.py`). Известные CVE (алгоритмическая путаница, DoS через JWE) здесь не
эксплуатируются — используется только HS256, без JWE и асимметричных ключей.
Если апгрейд когда-нибудь понадобится, заменой является `pyjwt`.
"""

import bcrypt
from jose import jwt

from app.clock import utcnow

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 12 * 60

# bcrypt молча обрезает ввод на 72 байтах (36 кириллических символов, т.к. в
# UTF-8 это 2 байта на букву) — два пароля с общим 72-байтным префиксом
# становятся взаимозаменяемыми, и смена длинного пароля правкой хвоста тихо
# ни на что не влияет. Валидируем на входе, чтобы это не выяснялось на
# инциденте.
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    if len(password.encode()) > BCRYPT_MAX_BYTES:
        raise ValueError(f"пароль длиннее {BCRYPT_MAX_BYTES} байт")
    # Cost зафиксирован явно (12), а не оставлен на дефолт bcrypt.gensalt():
    # _DUMMY_HASH в app/api/auth.py считается один раз при импорте и должен
    # оставаться неотличимым по времени от хешей реальных пользователей.
    # Если библиотека когда-нибудь сменит дефолтный cost, у уже сохранённых
    # в БД хешей (cost 12) и у свежего dummy-хеша (новый дефолт) разойдётся
    # время bcrypt.checkpw — и тайминг-защита сломается молча.
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    # Битый или пустой хеш в БД — это 401 (не найден/не подходит), а не 500.
    # bcrypt 4.2.1 бросает ValueError("Invalid salt") на явном мусоре и
    # pyo3_runtime.PanicException на хешах с обрезанной солью (длина ~8–29
    # символов после префикса) — вторая наследуется от BaseException, а не
    # от Exception, и её не поймает ни `except Exception` в эндпоинте, ни
    # ServerErrorMiddleware Starlette. Ловим широко и намеренно, но не глушим
    # сигналы прерывания процесса.
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def create_access_token(user_id: int, role: str, *, secret: str) -> str:
    # `secret` — keyword-only: `role` и `secret` — соседние параметры одного
    # типа (str), и при позиционном вызове их перестановка молча подписала бы
    # токен строкой роли вместо секрета, пройдя проверку типов.
    if not secret:
        raise ValueError("secret не может быть пустым")

    # `.timestamp()` даёт корректный UTC-эпох только потому, что
    # `app.clock.utcnow()` возвращает timezone-aware datetime в UTC. Если
    # utcnow() когда-нибудь станет naive (например, ради совместимости с
    # naive-колонками БД), `.timestamp()` начнёт трактовать время как
    # локальное для процесса — и exp каждого токена тихо сдвинется на
    # величину локального TZ.
    expire = utcnow().timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = {
        "user_id": user_id,
        # role в токене — информационная подсказка, не источник авторизации.
        # require_role проверяет роль из БД на каждый запрос: если её когда-
        # нибудь начнут читать отсюда «ради экономии запроса», деактивация
        # или смена роли перестанет действовать немедленно.
        "role": role,
        "exp": int(expire),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=[ALGORITHM])
