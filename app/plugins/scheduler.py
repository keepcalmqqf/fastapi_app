"""定时任务调度插件：cron 触发器，负责「到点投递」，执行复用异步任务插件。

设计取舍：
- 本插件只做触发器：按 cron 表达式计算下次触发时间，到点调用
  `task_manager.submit_named` 投递具名任务。任务执行、重试、结果观测全部走
  `app/plugins/tasks.py` 的既有链路（memory 进程内 / redis Redis Streams 双后端），
  不重复建设执行器。
- 关闭（ENABLE_SCHEDULER=false）时 `setup` 不安路由、lifespan 不启循环，行为与
  未集成完全一致；`@scheduler.cron` 装饰器仍然可用（只登记不触发），任务定义模块
  无需感知开关。
- 注册方式两种：
  - `@scheduler.cron("0 3 * * *", name="cleanup")` 装饰异步处理器：同时把处理器注册进
    task_manager（等价于 `@task_manager.task(name)`）并登记触发计划；
  - `scheduler.schedule("已注册名", "*/5 * * * *", args=..., kwargs=...)`：为已在
    task_manager 注册的任务追加触发计划。
- cron 表达式按 `SCHEDULER_TIMEZONE`（默认 UTC）求值；项目时间源统一为应用侧 UTC，
  比较与记录均用 `utc_now()`。
- 多实例/多 worker 防重复：触发前向 Redis 写锁
  `sched:lock:<任务名>:<触发时刻 epoch>`（SET NX EX），同一期只有一个实例能抢到
  投递权；Redis 不可用时 fail-open（照常投递并告警，漏跑比重复执行的代价更高）。
- 错过补偿：进程停机期间错过的周期不补跑，重启后从「下一个未来触发点」继续，
  避免停机积压的任务在启动瞬间风暴式补投。
- 启停由应用 lifespan 负责（先启 task_manager 再启 scheduler；先停 scheduler 再停
  task_manager），本插件不反向耦合 main.py。
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import APIRouter, Depends, FastAPI
from redis.asyncio import Redis

from app.api import API_V1_PREFIX
from app.core import result
from app.core.redis import create_redis
from app.core.result import Result
from app.core.settings import settings
from app.models import User
from app.models.base import utc_now
from app.plugins import tasks as tasks_plugin
from app.plugins.tasks import task_manager

logger = logging.getLogger("fastapi_app")

# 分布式触发锁的键前缀：键内带触发时刻，同一任务不同周期互不干扰
_LOCK_PREFIX = "sched:lock:"

# 触发循环的最长单次休眠秒数：休眠期间新登记的任务最多延迟该时长被纳入调度
_MAX_SLEEP_SECONDS = 60.0


@dataclass
class ScheduledJob:
    """一条定时计划：目标具名任务 + cron 表达式 + 固定参数 + 运行观测字段。"""

    name: str
    cron_expr: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    retries: int = 0
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_job_id: str | None = None


class Scheduler:
    """cron 调度器：单例 `scheduler` 由本模块导出，任务定义模块经装饰器登记。

    测试应构造独立实例（而非操作单例），避免跨用例污染。
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ScheduledJob] = {}
        self._task: asyncio.Task | None = None
        # Event 与队列同理：延迟到 start()（运行时循环上）创建，避开模块导入期的循环绑定
        self._wake: asyncio.Event | None = None
        self._started = False
        self._stopping = False
        # 触发锁客户端（测试可经 set_redis 注入 FakeRedis；生产懒建）
        self._redis: Redis | None = None
        self._tz: ZoneInfo | None = None

    def set_redis(self, client: Redis | None) -> None:
        """注入 Redis 客户端（测试用；生产由 _acquire_lock 内部懒建）。"""
        self._redis = client

    # ------------------------------------------------------------------ 注册
    def cron(
        self,
        expr: str,
        *,
        name: str | None = None,
        args: tuple = (),
        kwargs: dict | None = None,
        retries: int = 0,
    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """装饰器：注册异步处理器为具名任务并登记 cron 触发计划。

        表达式在装饰时即校验，非法表达式在模块导入期就失败，不带病启动。
        """

        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            task_name = name or fn.__name__
            task_manager.register(task_name, fn)
            self.schedule(task_name, expr, args=args, kwargs=kwargs, retries=retries)
            return fn

        return decorator

    def schedule(
        self,
        name: str,
        expr: str,
        *,
        args: tuple = (),
        kwargs: dict | None = None,
        retries: int = 0,
    ) -> ScheduledJob:
        """为已注册的具名任务登记触发计划；同名重复登记视为更新计划。"""
        if not croniter.is_valid(expr):
            raise ValueError(f"非法 cron 表达式: {expr!r}（任务 {name}）")
        job = ScheduledJob(
            name=name,
            cron_expr=expr,
            args=args,
            kwargs=kwargs or {},
            retries=max(0, retries),
        )
        self._jobs[name] = job
        # 已在运行中则立刻补算下次触发点（未启动时由 start() 统一计算）
        if self._started:
            job.next_run_at = self._next_after(job, utc_now())
            self._wakeup()
        return job

    def unschedule(self, name: str) -> bool:
        """移除一条触发计划（不注销 task_manager 里的任务本身）。"""
        removed = self._jobs.pop(name, None) is not None
        if removed:
            self._wakeup()
        return removed

    # ------------------------------------------------------------------ 触发计算
    def _timezone(self) -> ZoneInfo:
        if self._tz is None:
            try:
                self._tz = ZoneInfo(settings.SCHEDULER_TIMEZONE)
            except Exception:  # noqa: BLE001 - 配置错误的时区不应阻断启动，回退 UTC
                logger.warning(
                    "SCHEDULER_TIMEZONE=%s 无法识别，回退为 UTC", settings.SCHEDULER_TIMEZONE
                )
                self._tz = ZoneInfo("UTC")
        return self._tz

    @staticmethod
    def _as_aware_utc(value: datetime) -> datetime:
        """把项目约定的 naive UTC 时间视为 UTC（aware 时间则直接归一到 UTC）。

        utc_now() 返回 naive UTC（匹配 MySQL DATETIME 列），而 croniter 与
        timestamp() 需要 aware 时间：naive 时间直接 astimezone/timestamp 会被
        按系统本地时区解释，导致触发点整体偏移若干小时。
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _next_after(self, job: ScheduledJob, base: datetime) -> datetime:
        """以 base（项目约定的 naive UTC）为基准计算下一次触发时刻（返回 naive UTC）。"""
        local = self._as_aware_utc(base).astimezone(self._timezone())
        nxt = croniter(job.cron_expr, local).get_next(datetime)
        return nxt.astimezone(timezone.utc).replace(tzinfo=None)

    def _seconds_until_next(self, now: datetime) -> float:
        """距最近一次触发的休眠秒数，夹在 [0.1, _MAX_SLEEP_SECONDS] 之间。"""
        delays = [
            (job.next_run_at - now).total_seconds()
            for job in self._jobs.values()
            if job.next_run_at is not None
        ]
        if not delays:
            return _MAX_SLEEP_SECONDS
        return min(max(min(delays), 0.1), _MAX_SLEEP_SECONDS)

    # ------------------------------------------------------------------ 启停
    async def start(self) -> None:
        if self._started:
            return
        # 导入任务定义模块：触发计划经 `@scheduler.cron` 在模块导入时登记，不导入则
        # 本进程不知道任何计划（memory 后端下 tasks 插件自身不会导入该模块）。
        # 模块缺失不应阻断启动，只告警。
        try:
            importlib.import_module(settings.TASK_TASKS_MODULE)
        except Exception:  # noqa: BLE001
            logger.warning(
                "导入任务模块 %s 失败，本次启动没有任何定时计划",
                settings.TASK_TASKS_MODULE,
                exc_info=True,
            )
        self._stopping = False
        self._wake = asyncio.Event()
        now = utc_now()
        for job in self._jobs.values():
            # 停机期间错过的周期不补跑，统一从下一个未来触发点继续
            job.next_run_at = self._next_after(job, now)
        self._task = asyncio.create_task(self._loop(), name="scheduler-loop")
        self._started = True
        logger.info("定时任务插件已启动：%d 条计划", len(self._jobs))

    async def stop(self) -> None:
        if not self._started:
            return
        self._stopping = True
        self._wakeup()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        self._started = False
        self._stopping = False
        logger.info("定时任务插件已停止")

    def _wakeup(self) -> None:
        if self._wake is not None:
            self._wake.set()

    # ------------------------------------------------------------------ 触发循环
    async def _loop(self) -> None:
        while not self._stopping:
            now = utc_now()
            for job in list(self._jobs.values()):
                if job.next_run_at is not None and job.next_run_at <= now:
                    scheduled = job.next_run_at
                    job.next_run_at = self._next_after(job, now)
                    await self._fire(job, scheduled)
            assert self._wake is not None
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._seconds_until_next(now))

    async def _fire(self, job: ScheduledJob, scheduled: datetime) -> None:
        """到点投递：先抢分布式锁再 submit_named，投递失败只记日志不阻断循环。"""
        if not await self._acquire_lock(job, scheduled):
            logger.info(
                "定时任务 %s 本期(%s)已由其他实例投递，跳过", job.name, scheduled.isoformat()
            )
            return
        try:
            job.last_job_id = await task_manager.submit_named(
                job.name, *job.args, retries=job.retries, **job.kwargs
            )
            job.last_run_at = utc_now()
            logger.info("定时任务已投递: %s (job_id=%s)", job.name, job.last_job_id)
        except Exception:  # noqa: BLE001 - 单次投递失败不应中断调度循环
            logger.exception("定时任务 %s 投递失败", job.name)

    async def _acquire_lock(self, job: ScheduledJob, scheduled: datetime) -> bool:
        """抢本期投递锁：抢到返回 True；Redis 不可用时 fail-open（照常投递）。"""
        if self._redis is None:
            try:
                self._redis = create_redis()
            except Exception:  # noqa: BLE001
                logger.warning("创建 Redis 客户端失败，定时任务 %s 跳过锁直接投递", job.name)
                return True
        key = f"{_LOCK_PREFIX}{job.name}:{int(self._as_aware_utc(scheduled).timestamp())}"
        try:
            acquired = await self._redis.set(key, "1", ex=settings.SCHEDULER_LOCK_TTL, nx=True)
        except Exception:  # noqa: BLE001
            logger.warning("定时任务 %s 抢锁失败（Redis 异常），照常投递", job.name)
            return True
        return bool(acquired)

    # ------------------------------------------------------------------ 观测
    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "timezone": settings.SCHEDULER_TIMEZONE,
            "jobs": [
                {
                    "name": job.name,
                    "cron": job.cron_expr,
                    "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
                    "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
                    "last_job_id": job.last_job_id,
                }
                for job in self._jobs.values()
            ],
        }

    def reset(self) -> None:
        """清空触发计划（仅供测试与开发调试，不注销 task_manager 的任务）。"""
        self._jobs.clear()
        self._tz = None


# 模块级单例：任务定义模块经 `@scheduler.cron(...)` 登记，lifespan 经此启停
scheduler = Scheduler()


def setup(app: FastAPI) -> None:
    """挂载定时任务插件：注册观测接口（触发循环的启停由 app lifespan 统一负责）。

    与 tasks 插件同理：启停不放进本函数，交给 lifespan 在 Redis 就绪后、
    task_manager 启动后再拉起调度循环，关闭时先停调度（不再产生新投递），
    再停 task_manager 让在途任务收尾。
    """
    if not settings.ENABLE_SCHEDULER:
        logger.info("定时任务插件未启用 (ENABLE_SCHEDULER=false)")
        return
    if not settings.ENABLE_TASKS and settings.TASK_BACKEND.strip().lower() == "memory":
        logger.warning(
            "ENABLE_SCHEDULER=true 但 ENABLE_TASKS=false：memory 后端的任务 worker 不会启动，"
            "定时投递的任务将积压在队列中无人消费"
        )

    _router = APIRouter(prefix="/scheduler", tags=["定时任务"])

    @_router.get(
        "",
        summary="定时计划列表（需 task:read）：cron 表达式、上次/下次触发时间、最近投递的 job_id",
        response_model=Result[dict],
    )
    async def scheduler_status(
        _: User = Depends(tasks_plugin.require_task_read),
    ) -> Result[dict]:
        return result.ok(data=scheduler.status(), message="请求成功")

    # 手动带版本前缀（插件不在 app.api 包内，不会被自动发现），与业务接口保持一致
    app.include_router(_router, prefix=API_V1_PREFIX)
    logger.info("插件已启用: scheduler (timezone=%s)", settings.SCHEDULER_TIMEZONE)
