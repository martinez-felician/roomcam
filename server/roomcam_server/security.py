"""Contraseñas, tokens y credenciales TURN."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

_ph = PasswordHasher()

# Alfabeto sin caracteres confusos (sin 0/O, 1/I/L).
PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_LENGTH = 8


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(pw_hash: str, password: str) -> bool:
    try:
        return _ph.verify(pw_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def new_device_id() -> str:
    return "dev_" + secrets.token_hex(8)


def new_pairing_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_LENGTH))


def normalize_pairing_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def make_access_token(secret: str, *, device_id: str, user_id: int, kind: str, minutes: int) -> str:
    now = int(time.time())
    payload = {"sub": device_id, "uid": user_id, "kind": kind, "iat": now, "exp": now + minutes * 60}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(secret: str, token: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub", "uid", "kind"]})


def turn_credentials(secret: str, device_id: str, ttl: int) -> tuple[str, str, int]:
    """Credenciales TURN temporales compatibles con coturn (use-auth-secret)."""
    expires = int(time.time()) + ttl
    username = f"{expires}:{device_id}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode(), expires
