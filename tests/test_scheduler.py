"""定时任务调度插件测试。

覆盖：装饰器注册与表达式校验、下次触发时间计算（含时区）、到点投递（复用
task_manager）、多实例 Redis 锁防重复、Redis 异常 fail-open、触发循环端到端执行、
插件经 lifespan 启停并暴露观测接口、关闭时不挂路由。
Scheduler/TaskManager 均用独立实例，避免单例跨用例污染（同 test_tasks.py 的思路）。
"""

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.models.base import utc_now
from app.plugins import scheduler as scheduler_plugin
from app.plugins.scheduler import Scheduler
from app.plugins.tasks import TaskManager, TaskState
from tests.conftest import FakeRedis


def _run(coro):
    """在独立事件循环里跑一段异步逻辑（与 test_tasks.py 同思路）。"""
    return asyncio.run(coro)


@asynccontextmanager
async def _scheduler_lifespan(app: FastAPI):
    """镜像生产的 lifespan 接线：ENABLE_SCHEDULER 开启时启停调度循环（与 app.main 一致）。"""
    if settings.ENABLE_SCHEDULER:
        await scheduler_plugin.scheduler.start()
    try:
        yield
    finally:
        if settings.ENABLE_SCHEDULER:
            await scheduler_plugin.scheduler.stop()


def _make_scheduler_app() -> FastAPI:
    """构造挂载了调度插件并接管调度循环生命周期的应用（用于集成测试）。"""
    scheduler_plugin.scheduler.reset()
    app = FastAPI(lifespan=_scheduler_lifespan)
    scheduler_plugin.setup(app)
    return app


async def _await_terminal(manager: TaskManager, task_id: str, timeout: float = 2.0) -> None:
    """轮询直到任务进入终态，超时则断言失败。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = manager._records.get(task_id)
        if record is not None and record.state in (TaskState.SUCCESS, TaskState.FAILED):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"任务 {task_id} 在 {timeout}s 内未结束")


# ---------------------------------------------------------------------- 注册
def test_cron_decorator_registers_handler_and_job(monkeypatch):
    monkeypatch.setattr(scheduler_plugin, "task_manager", TaskManager())
    sched = Scheduler()

    @sched.cron("0 3 * * *", name="daily_cleanup", retries=2)
    async def daily_cleanup() -> None:
        pass

    job = sched._jobs["daily_cleanup"]
    assert job.cron_expr == "0 3 * * *"
    assert job.retries == 2
    # 处理器同步注册进 task_manager，供 submit_named 按名解析
    assert scheduler_plugin.task_manager.resolve("daily_cleanup") is daily_cleanup


def test_cron_rejects_invalid_expression():
    sched = Scheduler()
    with pytest.raises(ValueError, match="非法 cron 表达式"):
        sched.schedule("whatever", "not a cron")


def test_schedule_supports_args_kwargs(monkeypatch):
    monkeypatch.setattr(scheduler_plugin, "task_manager", TaskManager())
    sched = Scheduler()
    job = sched.schedule("demo", "*/5 * * * *", args=(1, 2), kwargs={"k": "v"})
    assert job.args == (1, 2)
    assert job.kwargs == {"k": "v"}


# ------------------------------------------------------------------ 触发计算
def test_next_run_is_future_and_utc(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_TIMEZONE", "UTC")
    sched = Scheduler()
    job = sched.schedule("demo", "* * * * *")
    base = utc_now()
    nxt = sched._next_after(job, base)
    assert nxt > base
    # 项目时间约定：naive UTC（匹配 MySQL DATETIME 列）
    assert nxt.tzinfo is None


def test_next_run_respects_timezone(monkeypatch):
    """cron 按配置时区求值：Asia/Shanghai 的 03:00 对应 UTC 的 19:00。"""
    monkeypatch.setattr(settings, "SCHEDULER_TIMEZONE", "Asia/Shanghai")
    sched = Scheduler()
    job = sched.schedule("demo", "0 3 * * *")
    nxt = sched._next_after(job, utc_now())
    assert (nxt.hour, nxt.minute) == (19, 0)


def test_unknown_timezone_falls_back_to_utc(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_TIMEZONE", "Not/AZone")
    sched = Scheduler()
    job = sched.schedule("demo", "0 3 * * *")
    assert sched._next_after(job, utc_now()).hour == 3


# ---------------------------------------------------------------------- 投递
def test_fire_submits_named_task(monkeypatch):
    manager = TaskManager()
    monkeypatch.setattr(scheduler_plugin, "task_manager", manager)
    collected: list[str] = []

    async def scenario() -> Scheduler:
        @manager.task("demo")
        async def demo() -> None:
            collected.append("ran")

        await manager.start()
        try:
            sched = Scheduler()
            sched.set_redis(FakeRedis())
            job = sched.schedule("demo", "0 3 * * *")
            await sched._fire(job, utc_now())
            assert job.last_job_id is not None
            await _await_terminal(manager, job.last_job_id)
            return sched
        finally:
            await manager.stop()

    sched = _run(scenario())
    assert collected == ["ran"]
    job = sched._jobs["demo"]
    assert job.last_run_at is not None
    record = manager._records[job.last_job_id]
    assert record.state is TaskState.SUCCESS


def test_fire_dedup_across_instances(monkeypatch):
    """多实例并发触发同一期：共享 Redis 锁保证只有一个实例真正投递。"""
    manager = TaskManager()
    monkeypatch.setattr(scheduler_plugin, "task_manager", manager)
    submitted: list[str] = []

    async def scenario() -> None:
        @manager.task("demo")
        async def demo() -> None:
            pass

        await manager.start()
        try:
            shared_redis = FakeRedis()
            sched_a, sched_b = Scheduler(), Scheduler()
            sched_a.set_redis(shared_redis)
            sched_b.set_redis(shared_redis)
            job_a = sched_a.schedule("demo", "* * * * *")
            job_b = sched_b.schedule("demo", "* * * * *")
            scheduled = utc_now()
            await asyncio.gather(sched_a._fire(job_a, scheduled), sched_b._fire(job_b, scheduled))
            submitted.extend(j.last_job_id for j in (job_a, job_b) if j.last_job_id is not None)
        finally:
            await manager.stop()

    _run(scenario())
    assert len(submitted) == 1


def test_fire_fail_open_when_redis_error(monkeypatch):
    """Redis 异常时照常投递（漏跑比重复执行代价更高）。"""
    manager = TaskManager()
    monkeypatch.setattr(scheduler_plugin, "task_manager", manager)

    class BrokenRedis:
        async def set(self, *args, **kwargs):
            raise ConnectionError("模拟 Redis 不可用")

    async def scenario() -> Scheduler:
        @manager.task("demo")
        async def demo() -> None:
            pass

        sched = Scheduler()
        sched.set_redis(BrokenRedis())
        job = sched.schedule("demo", "0 3 * * *")
        await sched._fire(job, utc_now())
        return sched

    sched = _run(scenario())
    assert sched._jobs["demo"].last_job_id is not None


def test_fire_failure_does_not_raise(monkeypatch):
    """投递失败（任务未注册）只记日志，不向上抛、不阻断调度循环。"""
    manager = TaskManager()
    monkeypatch.setattr(scheduler_plugin, "task_manager", manager)

    async def scenario() -> Scheduler:
        sched = Scheduler()
        sched.set_redis(FakeRedis())
        job = sched.schedule("missing", "0 3 * * *")
        await sched._fire(job, utc_now())  # 不应抛 ValueError
        return sched

    sched = _run(scenario())
    assert sched._jobs["missing"].last_job_id is None


# ------------------------------------------------------------------ 触发循环
def test_loop_fires_due_job_and_recomputes_next(monkeypatch):
    manager = TaskManager()
    monkeypatch.setattr(scheduler_plugin, "task_manager", manager)
    collected: list[str] = []

    async def scenario() -> Scheduler:
        fired = asyncio.Event()

        @manager.task("demo")
        async def demo() -> None:
            collected.append("ran")
            fired.set()

        await manager.start()
        sched = Scheduler()
        sched.set_redis(FakeRedis())
        job = sched.schedule("demo", "0 3 * * *")
        try:
            await sched.start()
            # 把下次触发点拨到过去，模拟「到点」，让循环立即触发一次
            job.next_run_at = utc_now() - timedelta(seconds=1)
            sched._wakeup()
            await asyncio.wait_for(fired.wait(), timeout=2.0)
            return sched
        finally:
            await sched.stop()
            await manager.stop()

    sched = _run(scenario())
    assert collected == ["ran"]
    job = sched._jobs["demo"]
    assert job.last_job_id is not None
    # 触发后下次触发点已推进到未来
    assert job.next_run_at is not None and job.next_run_at > utc_now()


def test_start_skips_missed_periods(monkeypatch):
    """停机期间错过的周期不补跑：启动时统一从下一个未来触发点继续。"""
    monkeypatch.setattr(scheduler_plugin, "task_manager", TaskManager())

    async def scenario() -> Scheduler:
        sched = Scheduler()
        sched.set_redis(FakeRedis())
        job = sched.schedule("demo", "* * * * *")
        before = utc_now()
        await sched.start()
        try:
            assert job.next_run_at is not None and job.next_run_at > before
        finally:
            await sched.stop()
        return sched

    _run(scenario())


# ------------------------------------------------------------------ 插件集成
def test_scheduler_endpoint_lists_jobs(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SCHEDULER", True)
    # 注意顺序：_make_scheduler_app 会 reset 单例，任务须在构造 app 之后登记
    app = _make_scheduler_app()

    @scheduler_plugin.scheduler.cron("0 3 * * *", name="daily_cleanup")
    async def daily_cleanup() -> None:
        pass

    try:
        with TestClient(app) as client:
            resp = client.get("/api/v1/scheduler")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        jobs = {j["name"]: j for j in body["data"]["jobs"]}
        assert jobs["daily_cleanup"]["cron"] == "0 3 * * *"
        assert jobs["daily_cleanup"]["next_run_at"] is not None
    finally:
        scheduler_plugin.scheduler.reset()


def test_scheduler_disabled_registers_no_route(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SCHEDULER", False)
    app = _make_scheduler_app()
    with TestClient(app) as client:
        assert client.get("/api/v1/scheduler").status_code == 404
