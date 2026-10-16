"""异步任务插件测试。

覆盖：worker 执行任务并记录终态、失败重试、无重试失败记录、队列满拒绝、
插件经 lifespan 启停 worker 并暴露状态接口。TaskManager 作为自包含类单独单测，
插件级集成用全新 FastAPI 应用 + setup 验证 lifespan 接线，避免依赖 app.main 的导入时序。
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.plugins import tasks as tasks_plugin
from app.plugins.tasks import TaskManager, TaskState


@asynccontextmanager
async def _tasks_lifespan(app: FastAPI):
    """镜像生产的 lifespan 接线：ENABLE_TASKS 开启时启停 worker（与 app.main 一致）。"""
    if settings.ENABLE_TASKS:
        await tasks_plugin.task_manager.start()
    try:
        yield
    finally:
        if settings.ENABLE_TASKS:
            await tasks_plugin.task_manager.stop()


def _make_tasks_app() -> FastAPI:
    """构造一个挂载了任务插件、并接管 worker 生命周期的应用（用于集成测试）。"""
    tasks_plugin.task_manager.reset()
    app = FastAPI(lifespan=_tasks_lifespan)
    tasks_plugin.setup(app)
    return app


def _run(coro):
    """在独立事件循环里跑一段异步逻辑（与 conftest.run_db 同思路）。"""
    return asyncio.run(coro)


async def _await_terminal(manager: TaskManager, task_id: str, timeout: float = 2.0) -> None:
    """轮询直到任务进入终态，超时则断言失败。"""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        record = manager._records.get(task_id)
        if record is not None and record.state in (TaskState.SUCCESS, TaskState.FAILED):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"任务 {task_id} 在 {timeout}s 内未结束")


def test_submit_executes_handler_and_records_success(monkeypatch):
    monkeypatch.setattr(settings, "TASK_WORKERS", 2)
    collected: list[int] = []
    captured: dict = {}

    async def handler(value: int) -> None:
        collected.append(value)

    async def scenario() -> None:
        mgr = TaskManager()
        await mgr.start()
        try:
            tid = await mgr.submit(handler, 42)
            await _await_terminal(mgr, tid)
            captured["mgr"] = mgr
            captured["tid"] = tid
        finally:
            await mgr.stop()

    _run(scenario())
    assert collected == [42]
    record = captured["mgr"]._records[captured["tid"]]
    assert record.state is TaskState.SUCCESS
    # 关闭后 worker 归零
    assert captured["mgr"].status()["workers"] == 0


def test_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "TASK_WORKERS", 1)
    attempts: list[int] = []
    captured: dict = {}

    async def flaky() -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("boom")

    async def scenario() -> None:
        mgr = TaskManager()
        await mgr.start()
        try:
            tid = await mgr.submit(flaky, retries=2)
            await _await_terminal(mgr, tid)
            captured["mgr"] = mgr
            captured["tid"] = tid
        finally:
            await mgr.stop()

    _run(scenario())
    # 首次 + 两次重试 = 3 次执行
    assert len(attempts) == 3
    record = captured["mgr"]._records[captured["tid"]]
    assert record.state is TaskState.SUCCESS


def test_failure_without_retry_records_error(monkeypatch):
    monkeypatch.setattr(settings, "TASK_WORKERS", 1)
    captured: dict = {}

    async def bad() -> None:
        raise ValueError("nope")

    async def scenario() -> None:
        mgr = TaskManager()
        await mgr.start()
        try:
            tid = await mgr.submit(bad, retries=0)
            await _await_terminal(mgr, tid)
            captured["mgr"] = mgr
            captured["tid"] = tid
        finally:
            await mgr.stop()

    _run(scenario())
    record = captured["mgr"]._records[captured["tid"]]
    assert record.state is TaskState.FAILED
    assert "ValueError" in record.error


def test_queue_full_rejects_submit(monkeypatch):
    monkeypatch.setattr(settings, "TASK_MAX_QUEUE", 1)
    # 容量 1 且不启动 worker 消费，第二次入队应因满而拒绝
    mgr = TaskManager()

    async def slow() -> None:
        await asyncio.sleep(10)

    async def scenario() -> str:
        tid1 = await mgr.submit(slow)
        with pytest.raises(asyncio.QueueFull):
            await mgr.submit(slow)
        return tid1

    tid1 = _run(scenario())
    assert mgr._records[tid1].state is TaskState.PENDING
    failed = [r for r in mgr._records.values() if r.state is TaskState.FAILED]
    assert len(failed) == 1


def test_named_task_registry(monkeypatch):
    mgr = TaskManager()
    called: list[str] = []

    @mgr.task("greet")
    async def greet(name: str) -> None:
        called.append(name)

    async def scenario() -> str:
        await mgr.start()
        try:
            tid = await mgr.submit_named("greet", "world")
            await _await_terminal(mgr, tid)
            return tid
        finally:
            await mgr.stop()

    _run(scenario())
    assert called == ["world"]
    # 未注册名应抛 ValueError
    with pytest.raises(ValueError):
        _run(mgr.submit_named("missing"))


def test_plugin_lifespan_starts_and_stops_workers(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TASKS", True)
    monkeypatch.setattr(settings, "TASK_WORKERS", 2)

    done: list[int] = []

    async def echo(x: int) -> None:
        done.append(x)

    app = _make_tasks_app()
    # 注册须在 reset() 之后（_make_tasks_app 内部会 reset）
    tasks_plugin.task_manager.register("echo", echo)

    async def scenario() -> None:
        # 直接在测试事件循环里驱动 lifespan（与生产接线一致），提交/等待同循环，避免跨循环
        async with app.router.lifespan_context(app):
            assert tasks_plugin.task_manager.started is True
            tid = await tasks_plugin.task_manager.submit_named("echo", 5)
            await _await_terminal(tasks_plugin.task_manager, tid)
            assert done == [5]
        # 退出 lifespan 后 worker 已停止
        assert tasks_plugin.task_manager.started is False

    _run(scenario())


def test_status_endpoint_via_client(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TASKS", True)
    monkeypatch.setattr(settings, "TASK_WORKERS", 3)

    app = _make_tasks_app()

    with TestClient(app) as client:
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["data"]["enabled"] is True
        assert body["data"]["workers"] == 3
        assert isinstance(body["data"]["recent"], list)


def test_disabled_plugin_registers_nothing(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TASKS", False)
    app = FastAPI()
    tasks_plugin.setup(app)
    # 关闭时不挂载任何路由
    paths = [getattr(r, "path", None) for r in app.routes]
    assert all("/tasks" not in (p or "") for p in paths)
