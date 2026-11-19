"""Password hashing, JWT access tokens and opaque token helpers."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import get_settings

_hasher = PasswordHasher()  # Argon2id, library defaults (RFC 9106 low-memory profile)
_ALG = "HS256"

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str) -> str | None:
    """Return an error code or None. Kept deliberately simple and explainable (NIST 800-63B)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return "password_too_short"
    if len(password) > 256:
        return "password_too_long"
    if len(set(password)) < 4:
        return "password_too_simple"
    return None


def create_access_token(user_id: uuid.UUID, session_family: uuid.UUID) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "sid": str(session_family),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=s.access_token_ttl_seconds)).timestamp()),
        "typ": "access",
        "iss": "scriptorium",
    }
    return jwt.encode(payload, s.secret_key, algorithm=_ALG), s.access_token_ttl_seconds


def decode_access_token(token: str) -> dict:
    s = get_settings()
    payload = jwt.decode(token, s.secret_key, algorithms=[_ALG], issuer="scriptorium", options={"require": ["exp", "sub"]})
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
