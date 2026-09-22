from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from app.core.settings import settings

password_hash = PasswordHash.recommended()


def hash_password(plain: str) -> str:
    return password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return password_hash.verify(plain, hashed)


def create_access_token(subject: str, aud: str, expires_minutes: int | None = None) -> str:
    """签发访问令牌，payload 含 sub/aud/iat/exp/jti；audience 区分调用方身份。"""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "aud": aud, "exp": expire, "iat": now, "jti": uuid4().hex}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, aud: str) -> str:
    """签发刷新令牌，额外带 type="refresh" 标记，防止与访问令牌互相冒用。"""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": subject,
        "aud": aud,
        "exp": expire,
        "iat": now,
        "jti": uuid4().hex,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """返回 token 负载（含 sub/aud），无效或过期返回 None。

    verify_aud 关闭：aud 是否合法由 deps 按调用方身份校验，
    同时兼容没有 aud 字段的旧版令牌。
    """
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        return None
