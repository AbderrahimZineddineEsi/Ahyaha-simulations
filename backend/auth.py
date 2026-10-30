"""Passwords, JWTs and device keys."""
import hashlib
import hmac
import secrets
from datetime import timedelta

import bcrypt
import jwt

from backend import clock
from backend.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(settings.bcrypt_rounds)).decode()


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def make_token(user) -> str:
    expires = clock.epoch(clock.now() + timedelta(hours=settings.jwt_hours))
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": int(expires)}, settings.jwt_secret, algorithm="HS256")


def read_token(token: str):
    """The user id and role in a token, or None if it is bad or expired."""
    try:
        # Expiry is checked here, not by PyJWT, so that it follows the same clock as every other rule
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"verify_exp": False})
        if claims["exp"] <= clock.epoch(clock.now()):
            return None
        return int(claims["sub"]), claims["role"]
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None


# A blackbox's key is random and long, so a fast hash is enough (it is checked every second)
def new_device_key() -> str:
    return secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def check_key(key: str | None, hashed: str) -> bool:
    return key is not None and hmac.compare_digest(hash_key(key), hashed)
