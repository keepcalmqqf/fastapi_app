"""邮箱验证：签发验证令牌、投递验证链接、验证后置标记。"""

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_email_verify_token, decode_access_token
from app.core.settings import settings
from app.services import mailer

logger = logging.getLogger("fastapi_app")

_TOKEN_TYPE = "email_verify"


def build_link(token: str) -> str:
    return f"{settings.EMAIL_VERIFY_URL_BASE}?token={token}"


async def send_verification(*, aud: str, subject_id: int, email: str) -> None:
    """为指定主体签发验证令牌并投递验证邮件。

    投递失败只记日志：注册/创建账号已经成功，不能因为邮件服务不可用而回滚。
    """
    token = create_email_verify_token(str(subject_id), aud)
    try:
        await mailer.send_verification_email(email, build_link(token))
    except Exception:
        logger.warning("验证邮件投递失败: aud=%s id=%s", aud, subject_id, exc_info=True)


async def verify(db: AsyncSession, token: str, *, aud: str, loader: Any, label: str) -> Any:
    """校验验证令牌并把对应账号标记为已验证。

    `loader` 是按 id 取账号的 repository 函数（如 `user_repo.get_user_by_id`）。

    aud 必须与令牌中的 aud 一致（会员令牌不能验证后台账号）；
    type 必须是 email_verify，避免拿长效 refresh 令牌来越权改状态。
    """
    payload = decode_access_token(token)
    if payload is None or payload.get("type") != _TOKEN_TYPE:
        raise HTTPException(status_code=401, detail="验证链接无效或已过期")
    if payload.get("aud") != aud or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="验证链接无效或已过期")

    obj = await loader(db, int(payload["sub"]))
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    if not obj.email_verified:
        obj.email_verified = True
        await db.flush()
        await db.commit()
    return obj
