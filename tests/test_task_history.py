"""异步任务执行历史（task_run 表）测试。

覆盖：按 job_id 覆盖式 upsert、分页/过滤查询、按时间清理、service 层写库的
fail-safe（库异常不影响任务执行）、以及控制台的两个历史接口（需权限）。

落库默认被 conftest 的 autouse fixture 关掉（避免单测连真实 MySQL），
本文件显式打开并把会话工厂替换成测试库。
"""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.settings import settings
from app.models.base import utc_now
from app.plugins import tasks as tasks_plugin
from app.plugins.tasks import require_task_read, require_task_write
from app.repositories import task as task_repo
from app.services import task as task_service
from tests.conftest import TestingSessionLocal, override_get_db, run_db


class _SessionCtx:
    """把 service 层 `async with AsyncSessionLocal()` 接到测试库上。"""

    def __init__(self) -> None:
        self.session = TestingSessionLocal()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc_info) -> bool:
        await self.session.close()
        return False


@pytest.fixture
def history_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "TASK_PERSIST_HISTORY", True)
    monkeypatch.setattr(task_service, "AsyncSessionLocal", lambda: _SessionCtx())
    yield


def _base_fields(job_id: str, **overrides):
    fields = {
        "job_id": job_id,
        "name": "send_welcome_email",
        "backend": "memory",
        "state": "queued",
        "enqueued_at": utc_now(),
    }
    fields.update(overrides)
    return fields


def test_upsert_creates_then_updates_same_row():
    job_id = "job-1"

    async def scenario() -> None:
        async with TestingSessionLocal() as db:
            await task_repo.upsert(db, **_base_fields(job_id))
            await db.commit()
            first = await task_repo.get_by_job_id(db, job_id)
            assert first is not None and first.state == "queued"

            started = utc_now()
            finished = started + timedelta(milliseconds=120)
            await task_repo.upsert(
                db,
                job_id=job_id,
                state="success",
                started_at=started,
                finished_at=finished,
                duration_ms=120,
                result='{"sent": true}',
            )
            await db.commit()

            rows, total = await task_repo.list_runs(db, 1, 20)
            assert total == 1, "状态推进是覆盖同一行，不是新增一行"
            row = rows[0]
            assert row.state == "success"
            assert row.started_at == started
            assert row.duration_ms == 120
            assert row.result == '{"sent": true}'

    run_db(lambda db: scenario())


def test_list_runs_filters_by_name_state_and_time():
    now = utc_now()

    async def scenario() -> None:
        async with TestingSessionLocal() as db:
            await task_repo.upsert(db, **_base_fields("j-a", name="alpha", state="success"))
            await task_repo.upsert(db, **_base_fields("j-b", name="beta", state="failed"))
            await task_repo.upsert(
                db,
                **_base_fields(
                    "j-old", name="beta", state="success", enqueued_at=now - timedelta(days=5)
                ),
            )
            await db.commit()

            rows, total = await task_repo.list_runs(db, 1, 20, name="beta")
            assert total == 2
            assert all(r.name == "beta" for r in rows)

            rows, total = await task_repo.list_runs(db, 1, 20, state="failed")
            assert total == 1 and rows[0].job_id == "j-b"

            rows, total = await task_repo.list_runs(
                db, 1, 20, enqueued_at_from=now - timedelta(days=1)
            )
            assert total == 2, "5 天前的记录应被时间下界过滤掉"

            # 倒序：最新入队的在前
            rows, _ = await task_repo.list_runs(db, 1, 2)
            assert [r.job_id for r in rows] == ["j-b", "j-a"]

    run_db(lambda db: scenario())


def test_delete_before_removes_old_rows():
    now = utc_now()

    async def scenario() -> None:
        async with TestingSessionLocal() as db:
            await task_repo.upsert(db, **_base_fields("j-new", enqueued_at=now))
            await task_repo.upsert(
                db, **_base_fields("j-old", enqueued_at=now - timedelta(days=40))
            )
            await db.commit()

            deleted = await task_repo.delete_before(db, now - timedelta(days=30))
            await db.commit()
            assert deleted == 1

            rows, total = await task_repo.list_runs(db, 1, 20)
            assert total == 1 and rows[0].job_id == "j-new"

    run_db(lambda db: scenario())


def test_record_run_is_fail_safe(history_enabled):
    """写库异常不得冒泡：任务执行链路不该因历史表出问题而失败。"""

    async def scenario() -> None:
        await task_service.record_run(job_id="job-x", name="demo", state="queued", backend="memory")

        async def boom(db, **kwargs):
            raise RuntimeError("数据库不可用")

        original = task_repo.upsert
        task_repo.upsert = boom  # type: ignore[assignment]
        try:
            await task_service.record_run(
                job_id="job-y", name="demo", state="running", backend="memory"
            )
        finally:
            task_repo.upsert = original  # type: ignore[assignment]

        async with TestingSessionLocal() as db:
            rows, total = await task_repo.list_runs(db, 1, 20)
        assert total == 1 and rows[0].job_id == "job-x"

    run_db(lambda db: scenario())


def test_record_run_persists_result_as_json(history_enabled):
    async def scenario() -> None:
        started = utc_now()
        await task_service.record_run(
            job_id="job-r",
            name="demo",
            state="success",
            backend="redis",
            attempt=1,
            retries=2,
            started_at=started,
            finished_at=started + timedelta(milliseconds=350),
            result={"ok": True},
        )
        async with TestingSessionLocal() as db:
            row = await task_repo.get_by_job_id(db, "job-r")
        assert row is not None
        assert row.duration_ms == 350
        assert row.result == '{"ok": true}'
        assert row.attempt == 1 and row.retries == 2

    run_db(lambda db: scenario())


def _make_history_app() -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_task_read] = lambda: None
    app.dependency_overrides[require_task_write] = lambda: None
    tasks_plugin.setup(app)
    return app


def _seed(job_id: str, name: str, state: str, days_ago: int = 0) -> None:
    async def scenario() -> None:
        async with TestingSessionLocal() as db:
            await task_repo.upsert(
                db,
                job_id=job_id,
                name=name,
                backend="memory",
                state=state,
                enqueued_at=utc_now() - timedelta(days=days_ago),
            )
            await db.commit()

    run_db(lambda db: scenario())


def test_history_endpoints(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TASKS", True)
    _seed("h-1", "send_welcome_email", "success")
    _seed("h-2", "cleanup_expired_tokens", "failed")
    _seed("h-old", "send_welcome_email", "success", days_ago=40)

    app = _make_history_app()
    with TestClient(app) as client:
        resp = client.get("/api/v1/tasks/history", params={"page": 1, "page_size": 10})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 3
        # 倒序：最新在前
        assert body["data"]["items"][0]["job_id"] == "h-2"

        filtered = client.get(
            "/api/v1/tasks/history",
            params={"name": "send_welcome_email", "state": "success"},
        ).json()
        assert filtered["data"]["total"] == 2

        # 清理 30 天前：只删 h-old
        deleted = client.delete("/api/v1/tasks/history", params={"days": 30}).json()
        assert deleted["data"]["deleted"] == 1
        assert client.get("/api/v1/tasks/history").json()["data"]["total"] == 2


def test_history_endpoints_require_permission(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TASKS", True)
    app = FastAPI()
    app.dependency_overrides[get_db] = override_get_db
    tasks_plugin.setup(app)
    with TestClient(app) as client:
        assert client.get("/api/v1/tasks/history").status_code == 401
        assert client.delete("/api/v1/tasks/history", params={"days": 30}).status_code == 401
