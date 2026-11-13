import time

import jwt
import pytest

from app.core.config import settings
from app.core import security


def test_password_hash_roundtrip():
    hashed = security.hash_password("secret-123")
    assert hashed != "secret-123"
    assert security.verify_password("secret-123", hashed)
    assert not security.verify_password("wrong", hashed)


def test_admin_jwt_issue_and_decode():
    token = security.issue_admin_jwt(1, "admin")
    payload = security.decode_admin_jwt(token)
    assert payload is not None
    assert payload["sub"] == "1"
    assert payload["role"] == "admin"


def test_admin_jwt_expired():
    now = int(time.time())
    expired = jwt.encode(
        {"sub": "1", "role": "admin", "iat": now - 7200, "exp": now - 3600},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    assert security.decode_admin_jwt(expired) is None


def test_admin_jwt_tampered():
    token = security.issue_admin_jwt(1, "admin")
    header, payload, signature = token.split(".")
    broken_sig = ("B" if signature[0] != "B" else "C") + signature[1:]
    tampered = f"{header}.{payload}.{broken_sig}"
    assert security.decode_admin_jwt(tampered) is None


def test_admin_jwt_wrong_role():
    now = int(time.time())
    token = jwt.encode(
        {"sub": "1", "role": "user", "iat": now, "exp": now + 600},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    assert security.decode_admin_jwt(token) is None


def test_session_token_format():
    token = security.new_session_token()
    assert security.is_valid_session_token(token)
    assert not security.is_valid_session_token("xyz")
    assert not security.is_valid_session_token(None)
    assert not security.is_valid_session_token(token + "00")