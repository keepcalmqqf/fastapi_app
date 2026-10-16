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
  列表」概念，观测走 job_id + 队列深度(XLEN)/待处理(XPENDING)。
- memory 后端的启停嵌进应用 lifespan：先备好 Redis/DB 再启动 worker，关闭时先停 worker
  最后关资源；redis 后端的 Web 进程 start() 仅建投递客户端与消费组，消费进程独立运行。
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

from fastapi import APIRouter, FastAPI
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.api import API_V1_PREFIX
from app.core import result
from app.core.redis import create_redis
from app.core.result import Result
from app.core.settings import settings
from app.models.base import utc_now

logger = logging.getLogger("fastapi_app")

# 内存中保留的最近任务记录上限，超出后丢弃最旧的一条（仅用于观测，非业务数据）
_STATE_CAP = 1000
# redis 后端结果/状态哈希前缀
_RESULT_PREFIX = "task:result:"


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
            {"name": name, "state": "queued", "attempt": "0", "enqueued_at": utc_now().isoformat()},
        )
        return job_id

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
        await self._enqueue(item)
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
        try:
            await item.handler(*item.args, **item.kwargs)
        except Exception as exc:  # noqa: BLE001 - 任务异常需被记录，不应冒泡到事件循环
            if item.retries > 0:
                item.retries -= 1
                record.state = TaskState.PENDING
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
        """供接口使用：按后端返回运行状态（memory 为进程内计数，redis 为队列深度/待处理）。"""
        if not self._is_redis():
            return {"backend": "memory", **self.status()}
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
            "workers": 0,
            "queue_len": queue_len,
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

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """最近任务记录（按入队倒序）；不含调用参数，避免敏感数据经接口泄露。

        仅 memory 后端有意义；redis 后端无全局最近列表概念，返回空（请用 job_status 按 id 查）。
        """
        if self._is_redis():
            return []
        records = list(self._records.values())[-limit:]
        records.reverse()
        return [
            {
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
        summary="任务运行状态；传 job_id 可查单条任务状态/结果",
        response_model=Result[dict],
    )
    async def task_status(job_id: str | None = None) -> Result[dict]:
        data = await task_manager.astatus()
        if job_id:
            data["job"] = await task_manager.job_status(job_id)
        else:
            data["recent"] = task_manager.recent()
        return result.ok(data=data, message="请求成功")

    # 手动带版本前缀（插件不在 app.api 包内，不会被自动发现），与业务接口保持一致
    app.include_router(_router, prefix=API_V1_PREFIX)
    logger.info(
        "插件已启用: tasks (backend=%s, worker=%d)", settings.TASK_BACKEND, settings.TASK_WORKERS
    )
