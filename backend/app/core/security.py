import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import settings

SESSION_COOKIE = "pczy_session"
SESSION_MAX_AGE_DAYS = 30


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def issue_admin_jwt(admin_id: int, username: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "username": username,
        "role": "admin",
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_jwt(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    return payload


def decode_admin_jwt(token: str) -> dict | None:
    payload = decode_jwt(token)
    if payload is None or payload.get("role") != "admin":
        return None
    return payload


def new_session_token() -> str:
    return uuid.uuid4().hex


def is_valid_session_token(token: str | None) -> bool:
    return bool(token) and len(token) == 32 and all(c in "0123456789abcdef" for c in token)