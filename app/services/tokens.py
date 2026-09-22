import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Response

from app.core.security import create_access_token, create_refresh_token, decode_access_token
from app.core.settings import settings
from app.schemas.token import TokenOut

logger = logging.getLogger("fastapi_app")

ACCESS_TOKEN_TTL_SECONDS = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
REFRESH_TOKEN_TTL_SECONDS = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400

_ACCESS_COOKIE = "access_token"
_REFRESH_COOKIE = "refresh_token"


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """将双令牌写入 HttpOnly Cookie，Secure 按 COOKIE_SECURE 解析值设置。"""
    response.set_cookie(
        _ACCESS_COOKIE,
        access_token,
        max_age=ACCESS_TOKEN_TTL_SECONDS,
        httponly=True,
        path="/",
        samesite="lax",
        secure=settings.cookie_secure,
    )
    response.set_cookie(
        _REFRESH_COOKIE,
        refresh_token,
        max_age=REFRESH_TOKEN_TTL_SECONDS,
        httponly=True,
        path="/",
        samesite="lax",
        secure=settings.cookie_secure,
    )


async def issue_tokens(subject: str, aud: str, response: Response, redis: Any) -> TokenOut:
    """签发 access + refresh 令牌对：refresh 的 jti 登记到 Redis 供轮换/撤销校验，
    同时在响应上写入两个 HttpOnly Cookie。"""
    access_token = create_access_token(subject=subject, aud=aud)
    refresh_token = create_refresh_token(subject=subject, aud=aud)
    payload = decode_access_token(refresh_token)
    jti = payload.get("jti") if payload else None
    if jti and redis is not None:
        # 值记录 aud，便于排查；存在性即代表该 refresh jti 有效
        await redis.set(f"refresh:{jti}", aud, ex=REFRESH_TOKEN_TTL_SECONDS)
    _set_auth_cookies(response, access_token, refresh_token)
    return TokenOut(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_TTL_SECONDS,
    )


async def rotate_refresh_token(refresh_token: str, response: Response, redis: Any) -> TokenOut:
    """轮换刷新令牌：校验签名/过期/type 后，旧 jti 必须仍在 Redis 中（未撤销未轮换），
    随即删除旧 jti（一次性使用），按原 sub/aud 签发新对并刷新 Cookie。"""
    payload = decode_access_token(refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")
    jti = payload.get("jti")
    subject = payload.get("sub")
    aud = payload.get("aud")
    if not jti or not subject or not aud:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")
    stored = await redis.get(f"refresh:{jti}") if redis is not None else None
    if stored is None:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")
    await redis.delete(f"refresh:{jti}")
    return await issue_tokens(subject=subject, aud=aud, response=response, redis=redis)


async def revoke_tokens(access_token: str | None, refresh_token: str | None, redis: Any) -> None:
    """登出撤销：access 的 jti 写入黑名单（TTL = 剩余有效期秒数，到期自然过期），
    refresh 的 jti 从有效集合中删除。无法解析的令牌直接忽略。"""
    if redis is None:
        return
    now = int(datetime.now(timezone.utc).timestamp())
    if access_token:
        payload = decode_access_token(access_token)
        jti = payload.get("jti") if payload else None
        exp = payload.get("exp") if payload else None
        if jti and exp:
            remaining = int(exp) - now
            if remaining > 0:
                await redis.set(f"blacklist:{jti}", "1", ex=remaining)
    if refresh_token:
        payload = decode_access_token(refresh_token)
        jti = payload.get("jti") if payload else None
        if jti:
            await redis.delete(f"refresh:{jti}")
