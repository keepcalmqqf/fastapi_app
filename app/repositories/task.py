"""异步任务执行历史的数据访问层：只查询/flush，事务提交由 service 层负责。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TaskRun


async def get_by_job_id(db: AsyncSession, job_id: str) -> TaskRun | None:
    return await db.scalar(select(TaskRun).where(TaskRun.job_id == job_id))


# 可覆盖式更新的字段：任务状态推进时逐次写入，未提供的保持原值
_UPDATABLE = (
    "name",
    "state",
    "attempt",
    "retries",
    "started_at",
    "finished_at",
    "duration_ms",
    "error",
    "result",
)


async def upsert(db: AsyncSession, **fields: Any) -> TaskRun:
    """按 job_id 覆盖式写入一条执行记录；已存在则更新给定字段。

    之所以用「先查再插/更新」而不是数据库方言的 upsert（MySQL ON DUPLICATE KEY
    UPDATE / SQLite ON CONFLICT）：方言语法在两套库上不通用，而本表写入频率低、
    冲突窗口极小（同一 job_id 的状态推进是串行的），可读性优先。
    """
    job_id = fields["job_id"]
    run = await get_by_job_id(db, job_id)
    if run is None:
        run = TaskRun(**fields)
        db.add(run)
    else:
        for key in _UPDATABLE:
            if key in fields and fields[key] is not None:
                setattr(run, key, fields[key])
    await db.flush()
    return run


async def list_runs(
    db: AsyncSession,
    page: int,
    page_size: int,
    *,
    name: str | None = None,
    state: str | None = None,
    enqueued_at_from: datetime | None = None,
    enqueued_at_to: datetime | None = None,
) -> tuple[list[TaskRun], int]:
    """分页查询执行历史，按入队时间倒序；支持任务名、状态与入队时间区间过滤。"""
    conditions: list[Any] = []
    if name:
        conditions.append(TaskRun.name == name)
    if state:
        conditions.append(TaskRun.state == state)
    if enqueued_at_from is not None:
        conditions.append(TaskRun.enqueued_at >= enqueued_at_from)
    if enqueued_at_to is not None:
        conditions.append(TaskRun.enqueued_at <= enqueued_at_to)

    total = await db.scalar(select(func.count()).select_from(TaskRun).where(*conditions))
    result = await db.scalars(
        select(TaskRun)
        .where(*conditions)
        # 入队时间倒序，同刻入队的按 id 兜底，保证分页顺序稳定
        .order_by(TaskRun.enqueued_at.desc(), TaskRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.all()), total or 0


async def delete_before(db: AsyncSession, cutoff: datetime) -> int:
    """物理删除入队时间早于 cutoff 的历史记录，返回删除行数。"""
    deleted = await db.execute(delete(TaskRun).where(TaskRun.enqueued_at < cutoff))
    return int(deleted.rowcount or 0)
