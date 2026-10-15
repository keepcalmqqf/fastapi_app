from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def create(
    db: AsyncSession,
    *,
    actor_type: str,
    actor_id: int | None,
    action: str,
    target_type: str,
    target_id: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> AuditLog:
    log = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip=ip,
    )
    db.add(log)
    await db.flush()
    return log


async def list_logs(
    db: AsyncSession,
    page: int,
    page_size: int,
    *,
    action: str | None = None,
    actor_id: int | None = None,
    created_at_from: Any = None,
    created_at_to: Any = None,
) -> tuple[list[AuditLog], int]:
    """分页查询审计日志，按 id 倒序（最新在前）；支持动作、操作者与创建时间区间过滤。

    时间入参由 Schema 层归一化成 UTC 朴素时间，可直接与库内 created_at 比较；
    区间两端均为闭区间。
    """
    conditions: list[Any] = []
    if action:
        conditions.append(AuditLog.action == action)
    if actor_id is not None:
        conditions.append(AuditLog.actor_id == actor_id)
    if created_at_from is not None:
        conditions.append(AuditLog.created_at >= created_at_from)
    if created_at_to is not None:
        conditions.append(AuditLog.created_at <= created_at_to)

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*conditions))
    result = await db.scalars(
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.all()), total or 0
