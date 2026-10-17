"""异步任务执行历史：业务层（事务边界在此，负责 commit）。

写入定位是「尽力而为的旁路」：任务能否执行成功不该取决于历史能不能写进去
（库抖动、连接池打满都不该让发邮件之类的后台任务失败），所以 `record_run`
自己开会话、自己提交，异常只记日志。

`list_history` / `purge_history` 则复用请求链路的会话（走 `get_db` 依赖），
由接口层统一收口，读与删都在页面主动触发时才发生。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.settings import settings
from app.models import TaskRun
from app.models.base import utc_now
from app.repositories import task as task_repo

logger = logging.getLogger("fastapi_app")

# result / error 的落库上限：字段是 String(512)，超长只截前段（观测够用，不做全文存储）
_TEXT_LIMIT = 500


def _dump(value: Any) -> str | None:
    """把处理器返回值序列化成可入库的字符串；不可序列化时退化成 repr。"""
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text[:_TEXT_LIMIT]


def _clip(value: str | None) -> str | None:
    return value[:_TEXT_LIMIT] if value else value


async def record_run(
    *,
    job_id: str,
    name: str,
    state: str,
    backend: str,
    attempt: int = 0,
    retries: int = 0,
    enqueued_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    error: str | None = None,
    result: Any = None,
) -> None:
    """覆盖式写入一条执行记录（按 job_id）；失败只记日志。

    开关 `TASK_PERSIST_HISTORY` 关闭时直接返回（测试与不需要留痕的场景用）。
    """
    if not settings.TASK_PERSIST_HISTORY:
        return
    duration_ms: int | None = None
    if started_at is not None and finished_at is not None:
        duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    try:
        async with AsyncSessionLocal() as db:
            await task_repo.upsert(
                db,
                job_id=job_id,
                name=name[:64],
                state=state[:16],
                backend=backend[:16],
                attempt=attempt,
                retries=retries,
                enqueued_at=enqueued_at or utc_now(),
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                error=_clip(error),
                result=_dump(result),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - 历史写库失败绝不影响任务执行
        logger.warning("写入任务执行历史失败（不影响任务执行）", exc_info=True)


async def list_history(
    db: AsyncSession,
    page: int,
    page_size: int,
    *,
    name: str | None = None,
    state: str | None = None,
    enqueued_at_from: datetime | None = None,
    enqueued_at_to: datetime | None = None,
) -> tuple[list[TaskRun], int]:
    """分页查询执行历史（按入队时间倒序）。"""
    return await task_repo.list_runs(
        db,
        page,
        page_size,
        name=name,
        state=state,
        enqueued_at_from=enqueued_at_from,
        enqueued_at_to=enqueued_at_to,
    )


async def purge_history(db: AsyncSession, days: int) -> int:
    """清理 enqueued_at 早于「当前时间 - days 天」的历史，返回删除行数。

    本表只作观测留痕、量大且可再生成，故用物理删除而非软删（软删会让表只增不减，
    索引与扫描成本持续上升）。
    """
    cutoff = utc_now() - timedelta(days=days)
    deleted = await task_repo.delete_before(db, cutoff)
    await db.commit()
    logger.info("已清理 %d 条任务执行历史（保留 %d 天）", deleted, days)
    return deleted
