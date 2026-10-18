"""审计日志写入。

审计是旁路记录：自己开会话、自己提交，与业务事务完全隔离。
- 不跟随业务事务回滚——业务操作失败时「有人尝试过」本身就是审计价值
  （如登录失败、越权尝试）。
- 写失败只记日志、不影响业务结果——审计表出问题不能让账号管理这类核心操作失败，
  与 app/services/task.py::record_run 的任务执行历史同一套 fail-safe 写法。

早期实现直接复用请求的 AsyncSession 并 `commit()` / `rollback()`：commit 会提前
提交业务事务，rollback 会连带丢弃该会话里尚未提交的业务改动，与「不影响业务结果」
的承诺正好相反。改为独立会话后两者都不再可能。
"""

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.settings import settings
from app.repositories import audit as audit_repo

logger = logging.getLogger("fastapi_app")

# 审计表 target_id / ip 的列宽（app/models/audit.py）。
# 超长输入必须在写入前截断：MySQL 严格模式下超长会抛 DataError(1406)，异常被
# fail-safe 的 except 吞掉后整条审计静默丢失——登录失败的 target_id 是原始邮箱
# （最长 254），攻击者只要把邮箱填长，撞库留痕就一条都不落库。
_MAX_COLUMN_LEN = 64

# 动作标识：{领域}.{动作}
ACTION_CREATE_USER = "user.create"
ACTION_UPDATE_USER = "user.update"
ACTION_DELETE_USER = "user.delete"
ACTION_CHANGE_PASSWORD = "user.change_password"
ACTION_DELETE_MEMBER = "member.delete"
ACTION_CHANGE_MEMBER_PASSWORD = "member.change_password"
ACTION_LOGIN_FAILED = "auth.login_failed"


def client_ip(request: Any) -> str | None:
    """取请求来源 IP。

    默认只认直连对端。置于反向代理之后时，直连对端恒是网关地址，后果有两处：
    审计的「按 IP 复盘」能力归零，且 IP 维度防爆破会把一个出口的失败算到全部
    用户头上（NAT 出口下等于一人失败连坐一片）。这种部署需显式开启
    TRUST_X_FORWARDED_FOR 改从 X-Forwarded-For / X-Real-IP 取值。

    开关默认关闭是有意的：直接暴露在公网时该头可任意伪造，开了反而让审计与
    锁定可被攻击者操纵。取不到或头缺失时回退直连对端。
    """
    if settings.TRUST_X_FORWARDED_FOR:
        headers = getattr(request, "headers", None)
        if headers is not None:
            # X-Forwarded-For 形如 "client, proxy1, proxy2"：最左为原始客户端
            forwarded = headers.get("x-forwarded-for") or headers.get("x-real-ip")
            if forwarded:
                candidate = forwarded.split(",")[0].strip()
                if candidate:
                    return candidate
    client = getattr(request, "client", None)
    return getattr(client, "host", None)


def dumps(detail: dict | None) -> str | None:
    """变更要点序列化为 JSON 字符串（超长时截断，避免超出列宽）。"""
    if not detail:
        return None
    text = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    return text[:512]


def _fit(value: str | None, limit: int = _MAX_COLUMN_LEN) -> str | None:
    """按列宽截断：不截断会撞 DataError 进而让整条审计静默丢失。"""
    if value is None:
        return None
    return value[:limit]


async def record(
    *,
    actor_type: str,
    actor_id: int | None,
    action: str,
    target_type: str,
    target_id: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
) -> None:
    """写一条审计；失败只记日志。

    用独立会话提交，不接收调用方的 db：复用业务会话会把审计的成败与业务事务
    绑在一起（见模块 docstring）。
    """
    try:
        async with AsyncSessionLocal() as db:
            await audit_repo.create(
                db,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=_fit(target_id),
                detail=dumps(detail),
                ip=_fit(ip),
            )
            await db.commit()
    except Exception:
        # 审计写入失败不阻断业务：这里没有业务会话可回滚，也绝不能去动它
        logger.exception("审计日志写入失败（不影响业务结果）: action=%s", action)


async def list_logs(
    db: AsyncSession,
    page: int,
    page_size: int,
    *,
    action: str | None = None,
    actor_id: int | None = None,
    created_at_from: datetime | None = None,
    created_at_to: datetime | None = None,
) -> tuple[list[Any], int]:
    return await audit_repo.list_logs(
        db,
        page,
        page_size,
        action=action,
        actor_id=actor_id,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
    )
