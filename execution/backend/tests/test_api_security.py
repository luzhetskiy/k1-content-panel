import pytest

from app.api.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_is_not_plaintext():
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert hashed.startswith("$2b$")


def test_verify_accepts_correct_password():
    assert verify_password("secret123", hash_password("secret123")) is True


def test_verify_rejects_wrong_password():
    assert verify_password("wrong", hash_password("secret123")) is False


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
