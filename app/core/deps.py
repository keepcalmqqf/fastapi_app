import logging
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import Member, User
from app.repositories import member as member_repo
from app.repositories import user as user_repo

logger = logging.getLogger("fastapi_app")

bearer_scheme = HTTPBearer(auto_error=False)

# 令牌取自 HttpOnly Cookie 时的 CSRF 防护：写操作必须携带自定义请求头，
# 浏览器跨站表单/脚本无法附带该头（受 CORS 与安全策略限制），
# 从而无法冒用自动携带的 Cookie 发起写操作。
_CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSRF_HEADER = "x-requested-with"
_CSRF_HEADER_VALUE = "XMLHttpRequest"


def get_redis(request: Request) -> Any:
    """从应用状态取 Redis 客户端（lifespan 中初始化）。"""
    return getattr(request.app.state, "redis", None)


async def _check_blacklist(payload: dict, redis: Any) -> None:
    """令牌撤销校验：jti 命中黑名单即视为已登出，返回 401。

    Redis 异常时放行（fail-open）：黑名单是可用性优先的旁路校验，
    短暂失效只影响已撤销令牌的即时拦截，不影响签名与过期校验。
    """
    jti = payload.get("jti")
    if not jti or redis is None:
        return
    try:
        if await redis.get(f"blacklist:{jti}") is not None:
            raise HTTPException(status_code=401, detail="认证令牌已失效")
    except HTTPException:
        raise
    except Exception:
        logger.warning("Redis 黑名单查询失败，本次请求放行（fail-open）", exc_info=True)


async def _resolve_subject(
    request: Request | None,
    credentials: HTTPAuthorizationCredentials | None,
    audience: str,
    redis: Any,
) -> str:
    """校验令牌并返回 subject；audience 不匹配（如会员令牌访问后台接口）同样视为无效。

    取令牌顺序：Authorization Bearer 优先，其次 access_token Cookie；
    令牌来自 Cookie 且为写操作时，必须携带 X-Requested-With 请求头（CSRF 防护）。
    """
    from_cookie = False
    token: str | None = None
    if credentials is not None:
        token = credentials.credentials
    elif request is not None:
        token = request.cookies.get("access_token")
        from_cookie = token is not None
    if token is None:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    if (
        from_cookie
        and request is not None
        and request.method.upper() in _CSRF_METHODS
        and request.headers.get(_CSRF_HEADER) != _CSRF_HEADER_VALUE
    ):
        raise HTTPException(status_code=403, detail="缺少防跨站请求头（X-Requested-With）")
    payload = decode_access_token(token)
    subject = payload.get("sub") if payload else None
    # 旧版令牌没有 aud 字段，按 admin 兼容处理
    token_aud = payload.get("aud", "admin") if payload else None
    if token_aud != audience or not subject or not subject.isdigit():
        raise HTTPException(status_code=401, detail="认证令牌无效或已过期")
    if payload:
        await _check_blacklist(payload, redis)
    return subject


async def get_current_user(
    request: Request = None,  # type: ignore[assignment]
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    redis: Any = Depends(get_redis),
) -> User:
    """解析 admin 侧令牌并返回当前用户；也可在端点内手动 await（自举守卫场景）。

    手动调用时只传 credentials/db/redis 即可，request 缺省表示不做 CSRF 头检查。
    """
    subject = await _resolve_subject(request, credentials, "admin", redis)
    user = user_repo.get_user_by_id(db, int(subject))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    return user


async def get_current_member(
    request: Request = None,  # type: ignore[assignment]
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    redis: Any = Depends(get_redis),
) -> Member:
    subject = await _resolve_subject(request, credentials, "member", redis)
    member = member_repo.get_member_by_id(db, int(subject))
    if member is None or not member.is_active:
        raise HTTPException(status_code=401, detail="会员不存在或已禁用")
    return member
