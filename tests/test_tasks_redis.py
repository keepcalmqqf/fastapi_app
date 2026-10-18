"""分布式（Redis Streams）任务后端测试。

- 用 FakeStreamRedis 在内存模拟 XADD/XGROUP/XREADGROUP/XACK/XPENDING/XAUTOCLAIM/哈希等命令，
  无需真实 Redis 即可覆盖：入队、名称校验、worker 处理（成功/重试/失败/毒消息/未注册）、
  状态与 job 查询。
- 另有一个命中真实 Redis 的端到端用例，本地无 Redis 时自动跳过。
"""

import asyncio
import importlib
import json
import uuid

import pytest
from redis.exceptions import ResponseError

# 导入任务模块，填充 task_manager 注册表（worker 与实时用例按名称解析处理器）
import app.tasks  # noqa: F401
from app.core.redis import create_redis
from app.core.settings import settings
from app.plugins import tasks_worker
from app.plugins.tasks import TaskManager, ensure_group, task_manager

_STREAM = settings.TASK_STREAM
_GROUP = settings.TASK_GROUP


def _run(coro):
    return asyncio.run(coro)


def _payload(job_id, name, *, args=None, kwargs=None, attempt=0, max_retries=0):
    return {
        "job_id": job_id,
        "name": name,
        "args": args or [],
        "kwargs": kwargs or {},
        "attempt": attempt,
        "max_retries": max_retries,
    }


def _id_gt(a: str, b: str) -> bool:
    am, _, as_ = a.partition("-")
    bm, _, bs = b.partition("-")
    return (int(am), int(as_)) > (int(bm), int(bs))


class FakeStreamRedis:
    """内存版 Redis，实现任务链路实际用到的 Streams/哈希命令。"""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.groups: dict[tuple[str, str], dict] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"1000-{self._seq}"

    def _find(self, stream: str, entry_id: str) -> tuple[str, dict]:
        for eid, fields in self.streams.get(stream, []):
            if eid == entry_id:
                return eid, fields
        raise KeyError(entry_id)

    async def xadd(self, stream, fields, id="*", **kw):
        eid = self._next_id()
        self.streams.setdefault(stream, []).append((eid, dict(fields)))
        return eid

    async def xgroup_create(self, stream, group, id="$", mkstream=False):
        if (stream, group) in self.groups:
            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        self.streams.setdefault(stream, [])
        last = self.streams[stream][-1][0] if self.streams[stream] else "0-0"
        self.groups[(stream, group)] = {"last": last if id == "$" else id, "pending": {}}

    async def xreadgroup(self, group, consumer, streams, count=None, block=None, noack=False):
        result = []
        for stream, _start in streams.items():
            key = (stream, group)
            if key not in self.groups:
                raise ResponseError(f"NOGROUP {stream} {group}")
            g = self.groups[key]
            new = [(eid, f) for eid, f in self.streams.get(stream, []) if _id_gt(eid, g["last"])]
            if count:
                new = new[:count]
            msgs = []
            for eid, f in new:
                g["last"] = eid
                g["pending"][eid] = consumer
                msgs.append((eid, dict(f)))
            if msgs:
                result.append((stream, msgs))
        return result

    async def xack(self, stream, group, *ids):
        g = self.groups.get((stream, group))
        n = 0
        for i in ids:
            if g and i in g["pending"]:
                del g["pending"][i]
                n += 1
        return n

    async def xlen(self, stream):
        return len(self.streams.get(stream, []))

    async def xpending(self, stream, group):
        g = self.groups.get((stream, group))
        pend = g["pending"] if g else {}
        consumers: dict[str, int] = {}
        for c in pend.values():
            consumers[c] = consumers.get(c, 0) + 1
        return {
            "pending": len(pend),
            "min": None,
            "max": None,
            "consumers": [{"name": k, "pending": v} for k, v in consumers.items()],
        }

    async def xautoclaim(
        self, stream, group, consumer, min_idle_time, start_id="0-0", count=None, justid=False
    ):
        g = self.groups.get((stream, group))
        if not g:
            raise ResponseError(f"NOGROUP {stream} {group}")
        claimed = []
        for eid in list(g["pending"].keys()):
            g["pending"][eid] = consumer
            _eid, fields = self._find(stream, eid)
            claimed.append(eid if justid else (eid, dict(fields)))
        return "0-0", claimed, []

    async def zadd(self, key, mapping):
        z = self.zsets.setdefault(key, {})
        z.update(mapping)
        return len(mapping)

    async def zremrangebyrank(self, key, start, stop):
        z = self.zsets.get(key)
        if not z:
            return 0
        ordered = [m for m, _ in sorted(z.items(), key=lambda kv: kv[1])]
        n = len(ordered)
        lo = start if start >= 0 else max(0, n + start)
        hi = stop if stop >= 0 else n + stop
        removed = ordered[lo : hi + 1]
        for member in removed:
            z.pop(member, None)
        return len(removed)

    async def zrevrange(self, key, start, stop):
        z = self.zsets.get(key)
        if not z:
            return []
        ordered = [m for m, _ in sorted(z.items(), key=lambda kv: (-kv[1],))]
        hi = None if stop < 0 else stop
        return ordered[start:hi] if hi is None else ordered[start : hi + 1]

    async def hset(self, key, mapping=None, **kw):
        self.hashes.setdefault(key, {}).update(mapping or {})

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def expire(self, key, ttl):
        return True

    def pipeline(self):
        return _FakeStreamPipeline(self)

    async def aclose(self):
        pass


class _FakeStreamPipeline:
    """最小 pipeline 实现，供 `_recent_from_redis` 的批量读取路径使用。

    与 conftest 的 `_FakePipeline` 同思路：没有它，任务模块的批量分支在测试里会
    抛 AttributeError 静默降级到逐条读取，批量路径永远测不到。
    """

    def __init__(self, client) -> None:
        self._client = client
        self._ops: list = []

    def hgetall(self, key):
        self._ops.append(key)
        return self

    async def execute(self):
        results = [await self._client.hgetall(key) for key in self._ops]
        self._ops.clear()
        return results


@pytest.fixture(autouse=True)
def clean_registry():
    """快照并恢复 task_manager 注册表，避免 worker 用例污染全局。

    必须 autouse：本模块的 worker 用例靠 `task_manager.register(...)` 注入处理器，
    一旦某个用例忘了显式声明该夹具，它注册的处理器就会泄漏给后续用例；而
    `tasks.py:292` 对「注册表为空」和「注册表非空但查不到」的处理分支不同，
    泄漏会静默改变别人的断言路径。显式声明的方式已经漏过（毒消息用例就漏了），
    改成强制生效。
    """
    saved = dict(task_manager._registry)
    yield
    task_manager._registry.clear()
    task_manager._registry.update(saved)


@pytest.fixture
def redis_backend(monkeypatch):
    monkeypatch.setattr(settings, "TASK_BACKEND", "redis")


def _mk_manager(fake: FakeStreamRedis) -> TaskManager:
    mgr = TaskManager()
    mgr.set_redis(fake)
    return mgr


def test_redis_submit_named_enqueues(redis_backend):
    fake = FakeStreamRedis()
    mgr = _mk_manager(fake)
    job_id = _run(mgr.submit_named("send_welcome_email", 42))

    entries = fake.streams[_STREAM]
    assert len(entries) == 1
    payload = json.loads(entries[0][1]["data"])
    assert payload["name"] == "send_welcome_email"
    assert payload["args"] == [42]
    assert payload["attempt"] == 0

    result_hash = fake.hashes[f"task:result:{job_id}"]
    assert result_hash["state"] == "queued"
    job = _run(mgr.job_status(job_id))
    assert job["state"] == "queued"


def test_redis_submit_closure_rejected(redis_backend):
    mgr = _mk_manager(FakeStreamRedis())

    async def handler() -> None:
        pass

    with pytest.raises(RuntimeError):
        _run(mgr.submit(handler))


def test_redis_submit_named_unknown_name_when_registry_loaded(redis_backend):
    mgr = _mk_manager(FakeStreamRedis())
    mgr.register("known", _noop)
    with pytest.raises(ValueError):
        _run(mgr.submit_named("typo", 1))


async def _noop() -> None:
    return None


def test_redis_astatus(redis_backend):
    fake = FakeStreamRedis()
    mgr = _mk_manager(fake)
    fake.groups[(_STREAM, _GROUP)] = {"last": "0-0", "pending": {"1000-1": "c0"}}
    fake.streams[_STREAM] = [("1000-1", {"data": "{}"})]
    status = _run(mgr.astatus())
    assert status["backend"] == "redis"
    assert status["queue_len"] == 1
    assert status["pending"] == 1
    assert status["consumers"] == 1


def test_redis_recent_index_lists_latest_jobs(redis_backend):
    """redis 后端没有全局列表概念：投递时写 ZSET 索引，recent() 据此还原最近记录。"""
    fake = FakeStreamRedis()
    mgr = _mk_manager(fake)
    job_ids: list[str] = []

    async def scenario() -> None:
        for i in range(3):
            job_ids.append(await mgr.submit_named("send_welcome_email", i))
            # 间隔保证 ZSET score（入队时间戳）严格递增，顺序断言才稳定
            await asyncio.sleep(0.01)

    _run(scenario())

    items = _run(mgr.recent(2))
    assert [item["job_id"] for item in items] == [job_ids[2], job_ids[1]]
    assert items[0]["name"] == "send_welcome_email"
    assert items[0]["state"] == "queued"
    # 索引独立于结果哈希：哈希被清掉（TTL 到期）的条目不再出现在列表里
    fake.hashes.clear()
    assert _run(mgr.recent(10)) == []


def test_redis_recent_reads_hashes_in_one_batch(redis_backend):
    """结果哈希要走批量读取：逐条 hgetall 时 limit=100 就是 100 次串行往返。"""
    fake = FakeStreamRedis()
    mgr = _mk_manager(fake)
    job_ids: list[str] = []

    async def scenario() -> None:
        for i in range(3):
            job_ids.append(await mgr.submit_named("send_welcome_email", i))
            await asyncio.sleep(0.01)

    _run(scenario())

    batches: list[int] = []

    async def _spy_hgetall(keys):
        # 记录每次批量的键数：批量路径下只有 1 次调用，逐条路径下是 N 次
        batches.append(len(keys))
        return [await fake.hgetall(k) for k in keys]

    mgr._hgetall_batch = _spy_hgetall  # type: ignore[method-assign]
    items = _run(mgr.recent(10))
    assert [item["job_id"] for item in items] == list(reversed(job_ids))
    assert batches == [3], f"期望一次批量取回全部哈希，实际分批: {batches}"


def test_redis_recent_falls_back_when_batch_fails(redis_backend):
    """批量读取失败要退回逐条，且只丢失败的那条，不让整个列表变空。"""
    fake = FakeStreamRedis()
    mgr = _mk_manager(fake)
    job_ids: list[str] = []

    async def scenario() -> None:
        for i in range(3):
            job_ids.append(await mgr.submit_named("send_welcome_email", i))
            await asyncio.sleep(0.01)

    _run(scenario())

    async def _broken_batch(_keys):
        return None  # 与 _hgetall_batch 失败时的约定一致：返回 None 触发降级

    mgr._hgetall_batch = _broken_batch  # type: ignore[method-assign]
    items = _run(mgr.recent(10))
    assert [item["job_id"] for item in items] == list(reversed(job_ids))

    # 结果哈希 TTL 到期是常态：缺一条只丢该条，其余照常展示
    expired_key = f"task:result:{job_ids[0]}"
    fake.hashes.pop(expired_key)
    items = _run(mgr.recent(10))
    assert [item["job_id"] for item in items] == [job_ids[2], job_ids[1]]


def test_worker_process_success(redis_backend):
    fake = FakeStreamRedis()
    task_manager.register("ok_task", _ok_handler)
    payload = _payload("j1", "ok_task", args=[7])
    eid = "1000-1"
    fake.streams[_STREAM] = [(eid, {"data": json.dumps(payload)})]
    fake.groups[(_STREAM, _GROUP)] = {"last": "0-0", "pending": {eid: "c0"}}

    _run(tasks_worker._process(fake, eid, {"data": json.dumps(payload)}))

    h = fake.hashes["task:result:j1"]
    assert h["state"] == "success"
    assert json.loads(h["result"])["ok"] is True
    assert fake.groups[(_STREAM, _GROUP)]["pending"] == {}


def test_worker_process_retry_then_success(redis_backend):
    fake = FakeStreamRedis()
    calls: list[int] = []
    task_manager.register("flaky_task", _make_flaky(calls))
    payload = _payload("j2", "flaky_task", max_retries=1)
    eid = "1000-1"
    fake.streams[_STREAM] = [(eid, {"data": json.dumps(payload)})]
    fake.groups[(_STREAM, _GROUP)] = {"last": "0-0", "pending": {eid: "c0"}}

    _run(tasks_worker._process(fake, eid, {"data": json.dumps(payload)}))
    h = fake.hashes["task:result:j2"]
    assert h["state"] == "queued"
    assert h["attempt"] == "1"
    assert "last_error" in h
    assert fake.groups[(_STREAM, _GROUP)]["pending"] == {}
    assert len(fake.streams[_STREAM]) == 2

    retry_eid, retry_fields = fake.streams[_STREAM][1]
    fake.groups[(_STREAM, _GROUP)]["pending"][retry_eid] = "c0"
    _run(tasks_worker._process(fake, retry_eid, retry_fields))
    assert calls == [1, 2]
    assert fake.hashes["task:result:j2"]["state"] == "success"


def test_worker_process_failure_exhausted(redis_backend):
    fake = FakeStreamRedis()
    task_manager.register("bad_task", _bad_handler)
    payload = _payload("j3", "bad_task")
    eid = "1000-1"
    fake.streams[_STREAM] = [(eid, {"data": json.dumps(payload)})]
    fake.groups[(_STREAM, _GROUP)] = {"last": "0-0", "pending": {eid: "c0"}}

    _run(tasks_worker._process(fake, eid, {"data": json.dumps(payload)}))
    h = fake.hashes["task:result:j3"]
    assert h["state"] == "failed"
    assert "ValueError" in h["error"]
    assert fake.groups[(_STREAM, _GROUP)]["pending"] == {}


def test_worker_process_unknown_task(redis_backend):
    fake = FakeStreamRedis()
    payload = _payload("j4", "missing_task")
    eid = "1000-1"
    fake.groups[(_STREAM, _GROUP)] = {"last": "0-0", "pending": {eid: "c0"}}
    _run(tasks_worker._process(fake, eid, {"data": json.dumps(payload)}))
    h = fake.hashes["task:result:j4"]
    assert h["state"] == "failed"
    assert "未注册的任务" in h["error"]
    assert fake.groups[(_STREAM, _GROUP)]["pending"] == {}


def test_worker_process_poison_message(redis_backend):
    fake = FakeStreamRedis()
    eid = "1000-1"
    fake.groups[(_STREAM, _GROUP)] = {"last": "0-0", "pending": {eid: "c0"}}
    _run(tasks_worker._process(fake, eid, {"data": "not-json"}))
    assert fake.groups[(_STREAM, _GROUP)]["pending"] == {}
    assert fake.hashes == {}


def test_worker_consumer_processes_message(redis_backend):
    fake = FakeStreamRedis()
    processed: list[int] = []
    stop = asyncio.Event()

    async def handler(x: int) -> None:
        processed.append(x)
        stop.set()

    task_manager.register("cons_task", handler)
    fake.groups[(_STREAM, _GROUP)] = {"last": "0-0", "pending": {}}
    payload = _payload("j5", "cons_task", args=[7])
    _run(fake.xadd(_STREAM, {"data": json.dumps(payload)}))

    async def scenario():
        await asyncio.wait_for(tasks_worker._consumer(fake, "c0", stop), timeout=2)

    _run(scenario())
    assert processed == [7]
    assert fake.hashes["task:result:j5"]["state"] == "success"


async def _ok_handler(x: int) -> dict:
    return {"ok": True, "x": x}


def _make_flaky(calls: list[int]):
    async def flaky() -> str:
        calls.append(len(calls) + 1)
        if len(calls) < 2:
            raise RuntimeError("boom")
        return "done"

    return flaky


async def _bad_handler() -> None:
    raise ValueError("nope")


def _live_redis_available() -> bool:
    async def _ping() -> bool:
        r = create_redis()
        try:
            await r.ping()
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            await r.aclose()

    try:
        return asyncio.run(asyncio.wait_for(_ping(), timeout=1.0))
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _live_redis_available(), reason="需要本地 Redis (127.0.0.1:6379)")
def test_redis_streams_roundtrip_live(monkeypatch):
    """命中真实 Redis：投递 → 消费组读取 → 处理 → ACK → 状态 success。"""
    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setattr(settings, "TASK_BACKEND", "redis")
    monkeypatch.setattr(settings, "TASK_STREAM", f"test:jobs:{suffix}")
    monkeypatch.setattr(settings, "TASK_GROUP", f"test:grp:{suffix}")

    mgr = TaskManager()
    redis = create_redis()

    async def scenario() -> None:
        # 其它用例可能 reset() 清空过单例注册表；reload 以重新注册具名任务，做到自给自足
        importlib.reload(app.tasks)
        mgr.set_redis(redis)
        try:
            job_id = await mgr.submit_named("send_welcome_email", 99)
            await ensure_group(redis)
            resp = await redis.xreadgroup(
                settings.TASK_GROUP, "t0", {settings.TASK_STREAM: ">"}, count=1, block=1000
            )
            assert resp, "应读到一条消息"
            _stream, messages = resp[0]
            entry_id, fields = messages[0]
            await tasks_worker._process(redis, entry_id, fields)
            job = await mgr.job_status(job_id)
            assert job["state"] == "success"
        finally:
            mgr.set_redis(None)
            await redis.aclose()

    _run(scenario())
