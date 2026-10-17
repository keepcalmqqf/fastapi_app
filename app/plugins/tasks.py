"""异步任务插件：可切换「进程内」与「分布式(Redis Streams)」两种后端，开关由配置控制。

设计取舍：
- 关闭（ENABLE_TASKS=false）时 `setup` 既不安路由也不启 worker，行为与未集成完全一致。
- 后端由 `TASK_BACKEND` 选择：
  - `memory`：进程内有界队列 + N 个常驻 worker 协程。单机、Fire-and-forget，适合发邮件、
    清理、外部回调等；重启即丢，不保证可靠投递。
  - `redis`：Redis Streams 消费组。**分布式**：Web 进程只负责投递（XADD）与查询，消费由
    独立 worker 进程完成（见 `app/plugins/tasks_worker.py`，`python -m app.plugins.tasks_worker`
    启动）。跨进程/跨机、可恢复（掉线消费者的 pending 由 XAUTOCLAIM 抢回重投）、失败自动重试。
- **分布式后端只支持具名任务**：任务要跨进程序列化到 Redis，本地闭包无法序列化，因此
  `submit(handler, ...)` 在 redis 模式会抛错，必须用 `submit_named("名", *args)` 投递已在
  `TASK_TASKS_MODULE`（默认 `app.tasks`）注册的处理器。任务参数须可 JSON 序列化。
- 结果/状态：memory 后端在内存保留最近 N 条（`recent()`）；redis 后端按 `job_id` 存结果哈希
  （`task:result:<job_id>`，带 TTL），经 `job_status(job_id)` 查询。分布式队列没有「全局最近
  列表」概念，故投递时额外把 job_id 写进一张有上限的 ZSET 索引（`task:recent`），
  `recent()` 据此还原最近投递记录，观测面与 memory 后端保持一致。
- memory 后端的启停嵌进应用 lifespan：先备好 Redis/DB 再启动 worker，关闭时先停 worker
  最后关资源；redis 后端的 Web 进程 start() 仅建投递客户端与消费组，消费进程独立运行。
- 观测接口需 `task:read`、手动投递需 `task:write`（见 app/services/rbac.py 种子）：
  任务名与失败原因属内部实现细节，不应匿名可读；投递更要挡住未授权调用。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import API_V1_PREFIX
from app.core import result
from app.core.database import get_db
from app.core.rbac import require_permissions
from app.core.redis import create_redis
from app.core.result import Result
from app.core.settings import settings
from app.models import User
from app.models.base import utc_now
from app.schemas.page import Page, PageParams
from app.schemas.task import TaskDispatchIn, TaskDispatchOut, TaskHistoryOut, TaskHistoryQuery
from app.services import task as task_history

logger = logging.getLogger("fastapi_app")

# 内存中保留的最近任务记录上限，超出后丢弃最旧的一条（仅用于观测，非业务数据）
_STATE_CAP = 1000
# redis 后端结果/状态哈希前缀
_RESULT_PREFIX = "task:result:"
# redis 后端「最近投递」索引（ZSET: member=job_id, score=入队时间戳），同样按 _STATE_CAP 截断
_RECENT_KEY = "task:recent"

# 观测/投递接口的权限守卫：模块级常量便于测试用 dependency_overrides 绕过
require_task_read = require_permissions("task:read")
require_task_write = require_permissions("task:write")


async def ensure_group(redis: Redis) -> None:
    """确保 Stream 与消费组存在（幂等；组已存在时忽略 BUSYGROUP）。供 Web 端与 worker 共用。"""
    try:
        await redis.xgroup_create(settings.TASK_STREAM, settings.TASK_GROUP, id="$", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


# memory 后端的入队态叫 pending，执行历史与 redis 后端统一记为 queued
_HISTORY_STATES: dict[TaskState, str] = {TaskState.PENDING: "queued"}


@dataclass
class TaskRecord:
    """单条任务的观测记录（仅留存于内存，不落库）。"""

    task_id: str
    name: str
    state: TaskState = TaskState.PENDING
    enqueued_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    retries_left: int = 0


@dataclass
class _Item:
    """队列中的任务载荷：处理器 + 参数 + 重试计数。"""

    record: TaskRecord
    handler: Callable[..., Awaitable[Any]]
    args: tuple
    kwargs: dict
    retries: int = 0


class TaskManager:
    """进程内异步任务执行器：有界队列 + N 个常驻 worker 协程。

    单例 `task_manager` 由本模块导出，业务层 `from app.plugins.tasks import task_manager`
    后即可 `submit` / `submit_named`；测试可直接操控本类实例。
    """

    def __init__(self) -> None:
        # 队列延迟到 start() 再创建：asyncio.Queue 会在创建时绑定当前事件循环，而本单例
        # 在模块导入时就存在，若此刻建好会绑到导入时的循环；应用真正运行时处于另一个循环
        # （TestClient 的后台循环 / uvicorn 的循环），worker 一操作队列就抛
        # 「bound to a different event loop」。故在 start()（运行时循环上）惰性创建。
        self._queue: asyncio.Queue[_Item] | None = None
        self._workers: list[asyncio.Task] = []
        self._running: dict[str, asyncio.Task] = {}
        self._registry: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._records: dict[str, TaskRecord] = {}
        self._started = False
        self._stopping = False
        # redis 后端专用：投递/查询客户端（Web 进程）与当前后端标识
        self._redis: Redis | None = None
        self._backend = "memory"

    def _is_redis(self) -> bool:
        return settings.TASK_BACKEND.strip().lower() == "redis"

    def set_redis(self, client: Redis | None) -> None:
        """注入 Redis 客户端（测试用；生产由 start() 内部按配置自建）。"""
        self._redis = client

    async def _ensure_group(self, redis: Redis) -> None:
        await ensure_group(redis)

    def resolve(self, name: str) -> Callable[..., Awaitable[Any]] | None:
        """按名称取已注册的处理器（worker 执行时用）；不存在返回 None。"""
        return self._registry.get(name)

    async def _set_job(self, job_id: str, mapping: dict[str, Any]) -> None:
        """写入任务结果/状态哈希并刷新 TTL（redis 后端）。"""
        if self._redis is None:
            return
        await self._redis.hset(f"{_RESULT_PREFIX}{job_id}", mapping=mapping)
        await self._redis.expire(f"{_RESULT_PREFIX}{job_id}", settings.TASK_RESULT_TTL)

    async def _redis_enqueue(self, name: str, args: tuple, kwargs: dict, retries: int) -> str:
        """redis 后端：XADD 投递 + 落一条 queued 状态，返回 job_id。"""
        if self._redis is None:
            self._redis = create_redis()
        await self._ensure_group(self._redis)
        job_id = uuid.uuid4().hex
        enqueued_at = utc_now()
        payload = json.dumps(
            {
                "job_id": job_id,
                "name": name,
                "args": list(args),
                "kwargs": kwargs,
                "attempt": 0,
                "max_retries": max(0, retries),
            },
            ensure_ascii=False,
            default=str,
        )
        await self._redis.xadd(settings.TASK_STREAM, {"data": payload})
        await self._set_job(
            job_id,
            {
                "name": name,
                "state": "queued",
                "attempt": "0",
                "enqueued_at": enqueued_at.isoformat(),
            },
        )
        await self._track_recent(job_id)
        # 入库留痕：没起 worker 时「排队中但一直没被消费」也能在历史里看出来
        await task_history.record_run(
            job_id=job_id,
            name=name,
            state="queued",
            backend="redis",
            retries=max(0, retries),
            enqueued_at=enqueued_at,
        )
        return job_id

    async def _track_recent(self, job_id: str) -> None:
        """把 job_id 记入「最近投递」索引并截断到 _STATE_CAP（redis 后端）。

        索引只是观测用的旁路：写失败只告警，不影响任务本身已入队的事实。
        """
        if self._redis is None:
            return
        try:
            await self._redis.zadd(_RECENT_KEY, {job_id: utc_now().timestamp()})
            # 保留 score 最大的 _STATE_CAP 条，其余（最旧的）移除
            await self._redis.zremrangebyrank(_RECENT_KEY, 0, -(_STATE_CAP + 1))
        except Exception:  # noqa: BLE001 - 观测索引失败不应阻断投递
            logger.warning("写入最近任务索引失败（不影响任务投递）", exc_info=True)

    def _ensure_queue(self) -> None:
        """确保任务队列已存在（未启动即投递时惰性创建，绑定到当前循环）。"""
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=max(0, settings.TASK_MAX_QUEUE))

    @property
    def started(self) -> bool:
        return self._started

    # ------------------------------------------------------------------ 注册
    def register(
        self, name: str, handler: Callable[..., Awaitable[Any]] | None = None
    ) -> Callable[..., Awaitable[Any]]:
        """注册具名任务处理器；可作装饰器用 `@task_manager.register("name")`。"""
        if handler is not None:
            self._registry[name] = handler
            return handler

        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            self._registry[name] = fn
            return fn

        return decorator

    # 装饰器别名，语义更直观：`@task_manager.task("name")`
    def task(self, name: str) -> Callable[..., Awaitable[Any]]:
        return self.register(name)

    # ------------------------------------------------------------------ 投递
    async def submit(
        self,
        handler: Callable[..., Awaitable[Any]],
        *args: Any,
        retries: int = 0,
        name: str | None = None,
        **kwargs: Any,
    ) -> str:
        """投递一个任务；队列满时抛 asyncio.QueueFull（记录置为 failed）。

        `retries>0` 表示执行失败可自动重试的次数（不含首次）。

        redis 后端不支持本地闭包（无法跨进程序列化），会抛 RuntimeError，请改用 submit_named。
        """
        if self._is_redis():
            raise RuntimeError(
                "分布式(Redis)后端无法投递本地闭包：请用 submit_named 投递已在 "
                f"{settings.TASK_TASKS_MODULE} 注册的具名任务"
            )
        task_id = uuid.uuid4().hex
        record = TaskRecord(
            task_id=task_id,
            name=name or getattr(handler, "__name__", "anonymous"),
            retries_left=retries,
        )
        self._track(record)
        self._ensure_queue()
        item = _Item(record=record, handler=handler, args=args, kwargs=kwargs, retries=retries)
        try:
            await self._enqueue(item)
        finally:
            # 入队成功落 queued；队列满被拒时 _enqueue 已把状态置 failed，这里如实留痕
            await self._persist(record)
        return task_id

    def _track(self, record: TaskRecord) -> None:
        """登记任务记录，超出内存上限时丢弃最旧的一条。"""
        self._records[record.task_id] = record
        while len(self._records) > _STATE_CAP:
            oldest = next(iter(self._records))
            self._records.pop(oldest)

    async def submit_named(self, name: str, *args: Any, retries: int = 0, **kwargs: Any) -> str:
        """按注册名投递具名任务；未注册名抛 ValueError（memory）或直接入队（redis）。

        redis 后端：任务定义以 worker 侧的注册表为准，Web 端若已加载任务模块则做名称校验，
        未加载（注册表为空）时放行，由 worker 在执行时解析。
        """
        handler = self._registry.get(name)
        if self._is_redis():
            if handler is None and self._registry:
                raise ValueError(f"未注册的任务: {name}")
            return await self._redis_enqueue(name, args, kwargs, retries)
        if handler is None:
            raise ValueError(f"未注册的任务: {name}")
        return await self.submit(handler, *args, retries=retries, name=name, **kwargs)

    async def _enqueue(self, item: _Item) -> None:
        assert self._queue is not None, "任务队列未初始化，请先调用 start() 或 submit()"
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            item.record.state = TaskState.FAILED
            item.record.error = "任务队列已满，提交被拒绝"
            item.record.finished_at = utc_now()
            logger.warning("任务 %s 提交失败：队列已满", item.record.task_id)
            raise

    # ------------------------------------------------------------------ 启停
    async def start(self) -> None:
        if self._started:
            return
        if self._is_redis():
            # Web 进程：只建投递/查询客户端并确保消费组存在；消费由独立 worker 进程完成。
            # 加载任务模块以填充注册表，供 submit_named 做名称校验（模块缺失则告警放行）。
            self._redis = create_redis()
            await self._ensure_group(self._redis)
            self._backend = "redis"
            self._started = True
            try:
                importlib.import_module(settings.TASK_TASKS_MODULE)
            except Exception:  # noqa: BLE001 - 任务模块缺失不应阻断应用启动
                logger.warning(
                    "导入任务模块 %s 失败，submit_named 将跳过本地名称校验",
                    settings.TASK_TASKS_MODULE,
                    exc_info=True,
                )
            logger.info(
                "异步任务插件已启动（Redis Streams 后端：stream=%s group=%s；Web 进程仅投递/查询，"
                "消费由 python -m app.plugins.tasks_worker 启动的 worker 完成）",
                settings.TASK_STREAM,
                settings.TASK_GROUP,
            )
            return
        self._stopping = False
        # 队列在运行时循环上重新创建，保证与 worker 同属一个事件循环
        self._queue = asyncio.Queue(maxsize=max(0, settings.TASK_MAX_QUEUE))
        worker_count = max(1, settings.TASK_WORKERS)
        self._workers = [
            asyncio.create_task(self._worker(), name=f"task-worker-{i}")
            for i in range(worker_count)
        ]
        self._started = True
        logger.info("异步任务插件已启动：%d 个 worker", len(self._workers))

    async def stop(self) -> None:
        if not self._started:
            return
        if self._is_redis():
            if self._redis is not None:
                await self._redis.aclose()
                self._redis = None
            self._backend = "memory"
            self._started = False
            logger.info("异步任务插件已停止（Redis 后端投递客户端已关闭）")
            return
        self._stopping = True
        # 1) 让在途任务自然收尾（不强行打断），最多等待 TASK_SHUTDOWN_TIMEOUT 秒
        busy = list(self._running.values())
        if busy:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*busy, return_exceptions=True),
                    timeout=settings.TASK_SHUTDOWN_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning("异步任务关闭超时，强制取消 %d 个在途任务", len(busy))
        # 2) 取消仍存活的 worker（多为阻塞在 queue.get 的空闲 worker）
        for worker in self._workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._running.clear()
        self._started = False
        self._stopping = False
        logger.info("异步任务插件已停止")

    # ------------------------------------------------------------------ 留痕
    async def _persist(self, record: TaskRecord, *, result: Any = None) -> None:
        """把一条任务记录写进执行历史（fail-safe：失败只记日志，不影响任务本身）。

        历史表按 job_id 覆盖式更新，所以这里每推进一步状态就写一次，最终态为准。
        """
        await task_history.record_run(
            job_id=record.task_id,
            name=record.name,
            state=_HISTORY_STATES.get(record.state, record.state.value),
            backend=self._backend,
            retries=record.retries_left,
            enqueued_at=record.enqueued_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            error=record.error,
            result=result,
        )

    # ------------------------------------------------------------------ worker
    async def _worker(self) -> None:
        while True:
            if self._stopping:
                break
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                # 关闭阶段被取消（通常阻塞在 get 上），直接结束
                raise
            try:
                await self._execute(item)
            finally:
                self._queue.task_done()

    async def _execute(self, item: _Item) -> None:
        record = item.record
        record.state = TaskState.RUNNING
        record.started_at = utc_now()
        current = asyncio.current_task()
        if current is not None:
            self._running[record.task_id] = current
        await self._persist(record)
        value: Any = None
        succeeded = False
        try:
            value = await item.handler(*item.args, **item.kwargs)
            succeeded = True
        except Exception as exc:  # noqa: BLE001 - 任务异常需被记录，不应冒泡到事件循环
            if item.retries > 0:
                item.retries -= 1
                record.state = TaskState.PENDING
                record.error = f"{type(exc).__name__}: {exc}"
                try:
                    self._queue.put_nowait(item)
                except asyncio.QueueFull:
                    record.state = TaskState.FAILED
                    record.error = f"重试排队失败（队列已满），原错误：{type(exc).__name__}: {exc}"
                    logger.warning("任务 %s 重试排队失败", record.task_id)
            else:
                record.state = TaskState.FAILED
                record.error = f"{type(exc).__name__}: {exc}"
                logger.exception("异步任务执行失败：%s", record.task_id)
        else:
            record.state = TaskState.SUCCESS
        finally:
            if record.state in (TaskState.SUCCESS, TaskState.FAILED):
                record.finished_at = utc_now()
            self._running.pop(record.task_id, None)
            # 终态留痕（带上返回值）；重试中回到 PENDING 时也记一次，便于看出重试次数
            await self._persist(record, result=value if succeeded else None)

    # ------------------------------------------------------------------ 观测
    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "workers": sum(1 for w in self._workers if not w.done()),
            "queue_size": self._queue.qsize() if self._queue is not None else 0,
            "running": len(self._running),
            "tracked": len(self._records),
        }

    async def astatus(self) -> dict[str, Any]:
        """供接口使用：按后端返回运行状态（memory 为进程内计数，redis 为队列深度/待处理）。

        `registered` 列出本进程已知的任务名：控制台据此给出可投递任务的候选；
        redis 后端下 Web 进程未加载任务模块时为空（由 worker 侧解析），属预期。
        """
        registered = sorted(self._registry)
        if not self._is_redis():
            return {"backend": "memory", "registered": registered, **self.status()}
        queue_len = 0
        pending = 0
        consumers = 0
        if self._redis is not None:
            try:
                queue_len = await self._redis.xlen(settings.TASK_STREAM)
            except Exception:  # noqa: BLE001 - Redis 异常时降级为 0，不让观测接口 500
                logger.warning("读取队列长度失败", exc_info=True)
            try:
                summary = await self._redis.xpending(settings.TASK_STREAM, settings.TASK_GROUP)
                pending = int(summary.get("pending", 0))
                consumers = len(summary.get("consumers", []))
            except Exception:  # noqa: BLE001
                logger.warning("读取待处理条目失败", exc_info=True)
        return {
            "backend": "redis",
            "enabled": True,
            "stream": settings.TASK_STREAM,
            "group": settings.TASK_GROUP,
            "registered": registered,
            "workers": 0,
            # 队列深度（XLEN，含已消费未修剪的条目）与待处理（XPENDING，已派发未 ACK）
            "queue_len": queue_len,
            "queue_size": pending,
            "pending": pending,
            "consumers": consumers,
        }

    async def job_status(self, job_id: str) -> dict[str, Any]:
        """按 job_id 查询任务状态/结果（redis 读结果哈希，memory 读内存记录）。"""
        if self._is_redis():
            if self._redis is None:
                return {"job_id": job_id, "state": "unknown"}
            data = await self._redis.hgetall(f"{_RESULT_PREFIX}{job_id}")
            if not data:
                return {"job_id": job_id, "state": "unknown"}
            return {"job_id": job_id, **data}
        rec = self._records.get(job_id)
        if rec is None:
            return {"job_id": job_id, "state": "unknown"}
        return {
            "job_id": rec.task_id,
            "name": rec.name,
            "state": rec.state.value,
            "enqueued_at": rec.enqueued_at.isoformat(),
            "started_at": rec.started_at.isoformat() if rec.started_at else None,
            "finished_at": rec.finished_at.isoformat() if rec.finished_at else None,
            "error": rec.error,
        }

    async def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """最近任务记录（按入队倒序）；不含调用参数，避免敏感数据经接口泄露。

        memory 后端读进程内记录；redis 后端读 `task:recent` 索引里的 job_id，再逐条取结果
        哈希（索引只存 job_id，状态仍以结果哈希为准，故重试/完成后的最新状态都能反映）。
        """
        if self._is_redis():
            return await self._recent_from_redis(limit)
        records = list(self._records.values())[-limit:]
        records.reverse()
        return [
            {
                # job_id 与 redis 后端对齐，便于前端统一按 job_id 查询；task_id 保留兼容
                "job_id": r.task_id,
                "task_id": r.task_id,
                "name": r.name,
                "state": r.state.value,
                "enqueued_at": r.enqueued_at.isoformat(),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "error": r.error,
            }
            for r in records
        ]

    async def _recent_from_redis(self, limit: int) -> list[dict[str, Any]]:
        """redis 后端的最近记录：先取索引里的 job_id，再逐条读结果哈希。"""
        if self._redis is None:
            return []
        try:
            job_ids = await self._redis.zrevrange(_RECENT_KEY, 0, max(1, limit) - 1)
        except Exception:  # noqa: BLE001 - 观测接口降级为空列表，不 500
            logger.warning("读取最近任务索引失败", exc_info=True)
            return []
        items: list[dict[str, Any]] = []
        for raw_id in job_ids:
            job_id = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
            try:
                data = await self._redis.hgetall(f"{_RESULT_PREFIX}{job_id}")
            except Exception:  # noqa: BLE001 - 单条读取失败跳过，其余照常展示
                continue
            if not data:
                continue
            items.append({"job_id": job_id, "task_id": job_id, **data})
        return items

    def reset(self) -> None:
        """清空处理器注册表与记录（仅供测试与开发调试，不影响在途 worker）。"""
        self._registry.clear()
        self._records.clear()


# 模块级单例：业务层经此投递任务，测试可重置其内部状态
task_manager = TaskManager()


def setup(app: FastAPI) -> None:
    """挂载异步任务插件：注册观测接口（worker 启停由 app lifespan 统一负责）。

    worker 的启停不放进本函数，而是交给应用既有的 lifespan（位于 app.main）：先让
    lifespan 备好 Redis/DB 再启动 worker，关闭时先停 worker（此时 Redis 仍可用，在途
    任务可正常收尾）最后才关闭资源。这样任务插件完全自包含，仅依赖 lifespan 在
    ENABLE_TASKS 开启时调用 task_manager.start()/stop()，无需在本文件反向耦合 main.py。
    """
    if not settings.ENABLE_TASKS:
        logger.info("异步任务插件未启用 (ENABLE_TASKS=false)")
        return

    _router = APIRouter(prefix="/tasks", tags=["异步任务"])

    @_router.get(
        "",
        summary="任务运行状态与最近任务（需 task:read）；传 job_id 可查单条任务状态/结果",
        response_model=Result[dict],
    )
    async def task_status(
        job_id: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        _: User = Depends(require_task_read),
    ) -> Result[dict]:
        data = await task_manager.astatus()
        if job_id:
            data["job"] = await task_manager.job_status(job_id)
        else:
            data["recent"] = await task_manager.recent(limit)
        return result.ok(data=data, message="请求成功")

    @_router.get(
        "/history",
        summary="执行历史分页查询（需 task:read）：落库的 task_run 记录，重启不丢",
        response_model=Result[Page[TaskHistoryOut]],
    )
    async def task_history_list(
        params: PageParams = Depends(),
        query: TaskHistoryQuery = Depends(),
        db: AsyncSession = Depends(get_db),
        _: User = Depends(require_task_read),
    ) -> Result[Page[TaskHistoryOut]]:
        items, total = await task_history.list_history(
            db,
            params.page,
            params.page_size,
            name=query.name,
            state=query.state,
            enqueued_at_from=query.enqueued_at_from,
            enqueued_at_to=query.enqueued_at_to,
        )
        page = Page[TaskHistoryOut](
            total=total,
            page=params.page,
            page_size=params.page_size,
            items=[TaskHistoryOut.model_validate(i) for i in items],
        )
        return result.ok(data=page)

    @_router.delete(
        "/history",
        summary="清理执行历史（需 task:write）：删掉 enqueued_at 早于 N 天前的记录",
        response_model=Result[dict],
    )
    async def task_history_purge(
        days: int = Query(default=settings.TASK_HISTORY_RETENTION_DAYS, ge=1, le=3650),
        db: AsyncSession = Depends(get_db),
        _: User = Depends(require_task_write),
    ) -> Result[dict]:
        deleted = await task_history.purge_history(db, days)
        return result.ok(data={"deleted": deleted, "days": days}, message="历史已清理")

    @_router.post(
        "/dispatch",
        summary="手动投递一个已注册的具名任务（需 task:write），返回 job_id 供查状态",
        response_model=Result[TaskDispatchOut],
    )
    async def dispatch_task(
        payload: TaskDispatchIn,
        _: User = Depends(require_task_write),
    ) -> Result[TaskDispatchOut]:
        try:
            job_id = await task_manager.submit_named(
                payload.name, *payload.args, retries=payload.retries, **payload.kwargs
            )
        except ValueError as exc:
            # 未注册的具名任务：属调用方参数问题，转 400 而不是让它冒泡成 500
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.ok(
            data=TaskDispatchOut(name=payload.name, job_id=job_id), message="任务已投递"
        )

    # 手动带版本前缀（插件不在 app.api 包内，不会被自动发现），与业务接口保持一致
    app.include_router(_router, prefix=API_V1_PREFIX)
    logger.info(
        "插件已启用: tasks (backend=%s, worker=%d)", settings.TASK_BACKEND, settings.TASK_WORKERS
    )
