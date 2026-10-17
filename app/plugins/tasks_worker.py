"""分布式任务 worker：作为独立进程消费 Redis Streams 中的任务。

用法：
    python -m app.plugins.tasks_worker                 # 用默认消费者名与并发数
    python -m app.plugins.tasks_worker -c 8            # 单进程 8 个消费者
    python -m app.plugins.tasks_worker --consumer web1 # 指定消费者名前缀

设计要点：
- 与 Web 进程解耦：Web 只 XADD 投递（见 app/plugins/tasks.py 的 submit_named），
  本进程负责 XREADGROUP 消费、执行、XACK，可多进程/多机平行扩容。
- 消费模型：消费组 group 下每个消费者各自 `>` 读取（条目不重复分派）；`count=1` +
  阻塞读取，单消费者串行处理，靠「多消费者协程 × 多 worker 进程」提升并发。
- 可靠投递（at-least-once）：执行成功/最终失败才 XACK；处理中崩溃的条目留在 pending，
  由 sweeper 用 XAUTOCLAIM（空闲超过 TASK_CLAIM_MIN_IDLE_MS）抢回重投。
  注意 at-least-once 语义：极端下可能重复执行，任务处理器需幂等。
- 失败重试：attempt < max_retries 时以 attempt+1 重新 XADD 并 ACK 原条目；否则标记 failed。
- 优雅关闭：SIGINT/SIGTERM 置停止事件，消费者取消、sweeper 取消，在途任务跑完再退出。
- 任务参数经 JSON 序列化，须可 JSON 化；处理器从 task_manager 注册表按名称解析。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import logging
import os
import signal
import socket
from datetime import datetime, timezone
from typing import Any

from app.core.redis import create_redis
from app.core.settings import settings
from app.models.base import utc_now
from app.plugins.tasks import _RESULT_PREFIX, ensure_group, task_manager
from app.services import task as task_history

logger = logging.getLogger("fastapi_app")


def _now() -> str:
    return utc_now().isoformat()


def _parse_time(value: str | None) -> datetime | None:
    """把 ISO 串还原成 naive UTC（与历史表时间列同款，带偏移的一律换算到 UTC）。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


async def _history(
    job_id: str,
    name: str,
    state: str,
    *,
    attempt: int = 0,
    retries: int = 0,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    error: str | None = None,
    result: Any = None,
) -> None:
    """写一条执行历史（fail-safe，见 app/services/task.py）。"""
    await task_history.record_run(
        job_id=job_id,
        name=name,
        state=state,
        backend="redis",
        attempt=attempt,
        retries=retries,
        started_at=started_at,
        finished_at=finished_at,
        error=error,
        result=result,
    )


async def _touch(redis: Any, job_id: str, mapping: dict[str, Any]) -> None:
    """更新任务结果/状态哈希并刷新 TTL。"""
    key = f"{_RESULT_PREFIX}{job_id}"
    await redis.hset(key, mapping=mapping)
    await redis.expire(key, settings.TASK_RESULT_TTL)


async def _process(redis: Any, entry_id: str, fields: dict[str, Any]) -> None:
    """处理单条 Stream 消息：解析→执行→更新状态→ACK/重试。"""
    try:
        payload = json.loads(fields["data"])
    except (ValueError, TypeError, KeyError):
        # 消息体非法：直接 ACK 丢弃，避免毒消息无限重投堵塞队列
        logger.warning("任务消息体非法，已丢弃: entry=%s", entry_id)
        await redis.xack(settings.TASK_STREAM, settings.TASK_GROUP, entry_id)
        return

    job_id = payload.get("job_id", "")
    name = payload.get("name", "")
    args = payload.get("args", [])
    kwargs = payload.get("kwargs", {})
    attempt = int(payload.get("attempt", 0))
    max_retries = int(payload.get("max_retries", 0))

    handler = task_manager.resolve(name)
    if handler is None:
        await _touch(
            redis,
            job_id,
            {"state": "failed", "error": f"未注册的任务: {name}", "finished_at": _now()},
        )
        await redis.xack(settings.TASK_STREAM, settings.TASK_GROUP, entry_id)
        logger.error("未注册的任务: %s（entry=%s）", name, entry_id)
        return

    started_at = _now()
    await _touch(
        redis, job_id, {"state": "running", "attempt": str(attempt), "started_at": started_at}
    )
    await _history(
        job_id,
        name,
        "running",
        attempt=attempt,
        retries=max_retries,
        started_at=_parse_time(started_at),
    )
    try:
        value = await handler(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 任务异常需被记录并走重试/失败分支
        if attempt < max_retries:
            retry_payload = {**payload, "attempt": attempt + 1}
            await redis.xadd(
                settings.TASK_STREAM,
                {"data": json.dumps(retry_payload, ensure_ascii=False, default=str)},
            )
            await _touch(
                redis,
                job_id,
                {
                    "state": "queued",
                    "attempt": str(attempt + 1),
                    "last_error": f"{type(exc).__name__}: {exc}",
                },
            )
            await _history(
                job_id,
                name,
                "queued",
                attempt=attempt + 1,
                retries=max_retries,
                error=f"{type(exc).__name__}: {exc}",
            )
            await redis.xack(settings.TASK_STREAM, settings.TASK_GROUP, entry_id)
            logger.warning("任务 %s 第 %d 次执行失败，已重排重试", name, attempt + 1)
        else:
            finished_at = _now()
            await _touch(
                redis,
                job_id,
                {
                    "state": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "finished_at": finished_at,
                },
            )
            await _history(
                job_id,
                name,
                "failed",
                attempt=attempt,
                retries=max_retries,
                started_at=_parse_time(started_at),
                finished_at=_parse_time(finished_at),
                error=f"{type(exc).__name__}: {exc}",
            )
            await redis.xack(settings.TASK_STREAM, settings.TASK_GROUP, entry_id)
            logger.exception("任务 %s 执行失败且超过重试上限", name)
    else:
        finished_at = _now()
        mapping: dict[str, Any] = {"state": "success", "finished_at": finished_at}
        if value is not None:
            mapping["result"] = json.dumps(value, ensure_ascii=False, default=str)
        await _touch(redis, job_id, mapping)
        await _history(
            job_id,
            name,
            "success",
            attempt=attempt,
            retries=max_retries,
            started_at=_parse_time(started_at),
            finished_at=_parse_time(finished_at),
            result=value,
        )
        await redis.xack(settings.TASK_STREAM, settings.TASK_GROUP, entry_id)


async def _consumer(redis: Any, consumer: str, stop_event: asyncio.Event) -> None:
    """单个消费者：阻塞读取新消息并处理。"""
    while not stop_event.is_set():
        try:
            resp = await redis.xreadgroup(
                settings.TASK_GROUP,
                consumer,
                {settings.TASK_STREAM: ">"},
                count=1,
                block=settings.TASK_BLOCK_MS,
            )
        except Exception:  # noqa: BLE001 - 读取失败不致命，退避后继续
            logger.warning("XREADGROUP 失败，1s 后重试", exc_info=True)
            await asyncio.sleep(1)
            continue
        if not resp:
            continue
        for _stream_name, messages in resp:
            for entry_id, fields in messages:
                await _process(redis, entry_id, fields)


async def _sweeper(redis: Any, consumer: str, stop_event: asyncio.Event) -> None:
    """回收 sweeper：把掉线消费者遗留的 pending 条目抢回本消费者重投。"""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.TASK_RECLAIM_INTERVAL)
            return
        except asyncio.TimeoutError:
            pass
        cursor = "0-0"
        while not stop_event.is_set():
            try:
                next_cursor, messages, _deleted = await redis.xautoclaim(
                    settings.TASK_STREAM,
                    settings.TASK_GROUP,
                    consumer,
                    min_idle_time=settings.TASK_CLAIM_MIN_IDLE_MS,
                    start_id=cursor,
                    count=10,
                )
            except Exception:  # noqa: BLE001
                logger.warning("XAUTOCLAIM 失败，下个周期再试", exc_info=True)
                break
            for entry_id, fields in messages:
                await _process(redis, entry_id, fields)
            cursor = next_cursor
            if cursor in ("0-0", "0"):
                break


async def run_worker(consumer: str | None = None, concurrency: int | None = None) -> None:
    """启动一个 worker 进程：N 个消费者协程 + 1 个回收 sweeper，直到收到停止信号。"""
    consumer = consumer or settings.TASK_CONSUMER_NAME or f"{socket.gethostname()}-{os.getpid()}"
    concurrency = concurrency or max(1, settings.TASK_WORKERS)

    # 加载任务模块以填充 task_manager 注册表（否则无法按名称解析处理器）
    try:
        importlib.import_module(settings.TASK_TASKS_MODULE)
    except Exception:  # noqa: BLE001
        logger.warning(
            "导入任务模块 %s 失败，worker 将无法解析具名任务",
            settings.TASK_TASKS_MODULE,
            exc_info=True,
        )

    redis = create_redis()
    await ensure_group(redis)

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    consumers = [
        asyncio.create_task(
            _consumer(redis, f"{consumer}-{i}", stop_event), name=f"task-consumer-{consumer}-{i}"
        )
        for i in range(concurrency)
    ]
    sweeper = asyncio.create_task(
        _sweeper(redis, f"{consumer}-sweep", stop_event), name=f"task-sweeper-{consumer}"
    )
    logger.info(
        "worker 已启动：consumer=%s 并发=%d stream=%s group=%s",
        consumer,
        concurrency,
        settings.TASK_STREAM,
        settings.TASK_GROUP,
    )
    try:
        await stop_event.wait()
    finally:
        logger.info("worker 收到停止信号，正在收尾...")
        for task in consumers:
            task.cancel()
        sweeper.cancel()
        await asyncio.gather(*consumers, sweeper, return_exceptions=True)
        await redis.aclose()
        logger.info("worker 已停止")


def main() -> None:
    parser = argparse.ArgumentParser(description="fastapi_app 分布式任务 worker")
    parser.add_argument("--consumer", default=None, help="消费者名前缀（默认 主机名-pid）")
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=None,
        help="本进程消费者协程数（默认 TASK_WORKERS）",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(args.consumer, args.concurrency))


if __name__ == "__main__":
    main()
