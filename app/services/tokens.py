import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Response

from app.core.revocation import is_subject_revoked
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
    # 主体级撤销优先于 jti 校验：账号被软删/禁用后不得再凭旧 refresh 换发新令牌
    if await is_subject_revoked(aud, subject, payload.get("iat"), redis):
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")
    # 取出并删除必须原子（GETDEL）：先 GET 再 DELETE 时，同一 refresh token 的两个
    # 并发请求（多标签页同时静默刷新）会都读到值、都通过校验、都签发新令牌对，
    # 「一次性使用」与「旧令牌重用即 401」的盗用检测在并发下形同虚设。
    #
    # Redis 不可用时 fail-open，与黑名单校验、主体撤销保持一致：令牌仍有签名与
    # 过期时间兜底，只是暂时失去即时撤销能力。此前这里是全项目唯一的 fail-closed
    # 旁路，Redis 一抖动就全站登录态集体掉线。
    verified = False
    if redis is not None:
        try:
            stored = await redis.getdel(f"refresh:{jti}")
        except Exception:  # noqa: BLE001
            logger.warning("刷新令牌 jti 校验失败，本次放行（fail-open）", exc_info=True)
        else:
            verified = True
            if stored is None:
                raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")
    if not verified:
        logger.warning("刷新令牌 jti 未校验：Redis 不可用，fail-open 放行 aud=%s", aud)
    return await issue_tokens(subject=subject, aud=aud, response=response, redis=redis)


async def revoke_tokens(access_token: str | None, refresh_token: str | None, redis: Any) -> None:
    """登出撤销：access 的 jti 写入黑名单（TTL = 剩余有效期秒数，到期自然过期），
    refresh 的 jti 从有效集合中删除。无法解析的令牌直接忽略。

    只作用于传入的这两个令牌（即当前设备），不影响该账号在其他设备上的会话；
    需要终止账号全部会话时用 `app.core.revocation.revoke_subject`。
    """
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
