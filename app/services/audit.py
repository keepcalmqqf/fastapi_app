"""审计日志写入。

审计有自己的事务边界：写完立即提交，不跟随业务事务回滚——
业务操作失败时「有人尝试过」本身就是审计价值（如登录失败、越权尝试）。

写入失败只记日志、不影响业务结果：审计属于旁路记录，不能因为审计表
出问题就让账号管理这类核心操作失败。
"""

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import audit as audit_repo

logger = logging.getLogger("fastapi_app")

# 动作标识：{领域}.{动作}
ACTION_CREATE_USER = "user.create"
ACTION_UPDATE_USER = "user.update"
ACTION_DELETE_USER = "user.delete"
ACTION_CHANGE_PASSWORD = "user.change_password"
ACTION_DELETE_MEMBER = "member.delete"
ACTION_CHANGE_MEMBER_PASSWORD = "member.change_password"
ACTION_LOGIN_FAILED = "auth.login_failed"


def client_ip(request: Any) -> str | None:
    """取请求来源 IP；反向代理部署时应改为读取 X-Forwarded-For。"""
    client = getattr(request, "client", None)
    return getattr(client, "host", None)


def dumps(detail: dict | None) -> str | None:
    """变更要点序列化为 JSON 字符串（超长时截断，避免超出列宽）。"""
    if not detail:
        return None
    text = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    return text[:512]


async def record(
    db: AsyncSession,
    *,
    actor_type: str,
    actor_id: int | None,
    action: str,
    target_type: str,
    target_id: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
) -> None:
    try:
        await audit_repo.create(
            db,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=dumps(detail),
            ip=ip,
        )
        await db.commit()
    except Exception:
        # 审计写入失败不阻断业务；回滚本会话，避免残留未提交状态影响后续请求
        logger.exception("审计日志写入失败（不影响业务结果）: action=%s", action)
        try:
            await db.rollback()
        except Exception:
            logger.warning("审计日志写入失败后的回滚也失败", exc_info=True)


async def list_logs(
    db: AsyncSession,
    page: int,
    page_size: int,
    *,
    action: str | None = None,
    actor_id: int | None = None,
) -> tuple[list[Any], int]:
    return await audit_repo.list_logs(db, page, page_size, action=action, actor_id=actor_id)
