import time
from datetime import timedelta

import pytest
from jose import jwt as jose_jwt

from app.api.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    BCRYPT_MAX_BYTES,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.clock import utcnow


def test_hash_is_not_plaintext():
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert hashed.startswith("$2b$")


def test_verify_accepts_correct_password():
    assert verify_password("secret123", hash_password("secret123")) is True


def test_verify_rejects_wrong_password():
    assert verify_password("wrong", hash_password("secret123")) is False


def test_verify_rejects_empty_hash_without_raising():
    """Пустой password_hash (например, повреждённая строка в БД) должен
    читаться как «пароль не подошёл», а не валить эндпоинт 500-й."""
    assert verify_password("secret123", "") is False


def test_verify_rejects_truncated_hash_without_raising():
    """bcrypt 4.2.1 бросает pyo3_runtime.PanicException на хешах с обрезанной
    солью (длина 8–29 символов после префикса `$2b$12$`) — эта строка длиной
    16 воспроизводит панику. PanicException наследуется от BaseException, а
    не от Exception, поэтому `except Exception` в эндпоинте её не поймает."""
    assert verify_password("secret123", "$2b$12$shortsalt") is False


def test_hash_password_accepts_72_byte_password():
    password = "a" * BCRYPT_MAX_BYTES
    hashed = hash_password(password)
    assert verify_password(password, hashed) is True


def test_hash_password_rejects_password_over_72_bytes():
    with pytest.raises(ValueError):
        hash_password("a" * (BCRYPT_MAX_BYTES + 1))


def test_hash_password_rejects_long_cyrillic_password():
    # Кириллица — 2 байта на символ в UTF-8: 37 символов = 74 байта > лимита.
    with pytest.raises(ValueError):
        hash_password("а" * 37)


def test_token_roundtrip():
    token = create_access_token(user_id=7, role="admin", secret="test-secret")
    payload = decode_access_token(token, secret="test-secret")
    assert payload["user_id"] == 7
    assert payload["role"] == "admin"


def test_token_rejects_wrong_secret():
    from jose import JWTError

    token = create_access_token(user_id=7, role="admin", secret="test-secret")
    with pytest.raises(JWTError):
        decode_access_token(token, secret="other-secret")


def test_expire_is_reasonable():
    """12 часов — рабочая смена. Короче — разлогинит посреди партии статей,
    длиннее — расширяет окно злоупотребления украденной cookie."""
    assert 60 <= ACCESS_TOKEN_EXPIRE_MINUTES <= 24 * 60


def test_exp_claim_is_now_plus_expiry():
    before = utcnow()
    token = create_access_token(user_id=1, role="admin", secret="test-secret")
    payload = decode_access_token(token, secret="test-secret")
    after = utcnow()

    expected_min = before.timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
    expected_max = after.timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert expected_min - 2 <= payload["exp"] <= expected_max + 2


def test_exp_claim_is_correct_regardless_of_local_timezone(monkeypatch):
    """`create_access_token` считает `exp` как `utcnow().timestamp() + ...`.
    Это корректно только пока `utcnow()` возвращает aware-datetime в UTC —
    aware `.timestamp()` даёт правильный эпох независимо от TZ процесса.
    Если `clock.utcnow()` когда-нибудь станет naive (например, ради
    совместимости с naive-колонками БД, как это случилось в другом
    проекте), naive `.timestamp()` начнёт трактовать время как локальное
    для процесса, и exp каждого токена тихо сдвинется на величину TZ. Тест
    держит TZ процесса не-UTC, чтобы такая регрессия не могла спрятаться за
    тем, что контейнер по умолчанию работает в UTC."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    try:
        before = utcnow()
        token = create_access_token(user_id=1, role="admin", secret="test-secret")
        payload = decode_access_token(token, secret="test-secret")
        after = utcnow()

        expected_min = before.timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
        expected_max = after.timestamp() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
        assert expected_min - 2 <= payload["exp"] <= expected_max + 2
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_expired_token_is_rejected():
    from jose import ExpiredSignatureError

    past = utcnow() - timedelta(minutes=1)
    token = jose_jwt.encode(
        {"user_id": 1, "role": "admin", "exp": int(past.timestamp())},
        "test-secret",
        algorithm=ALGORITHM,
    )
    with pytest.raises(ExpiredSignatureError):
        decode_access_token(token, secret="test-secret")


def test_create_access_token_rejects_empty_secret():
    with pytest.raises(ValueError):
        create_access_token(user_id=1, role="admin", secret="")


def test_create_access_token_secret_is_keyword_only():
    with pytest.raises(TypeError):
        create_access_token(1, "admin", "test-secret")
