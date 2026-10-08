"""cache 插件测试：@cached 命中与回退、invalidate 前缀清理、Redis 缺失时的降级。

cache 插件不是开关插件（始终可用），但此前没有任何测试覆盖，
这里用内存 FakeRedis 走通命中/回退/清理三条路径。
"""

import asyncio
from typing import cast

import pytest
from redis.asyncio import Redis

from app.plugins import cache as cache_plugin
from tests.conftest import fake_redis


class _BrokenRedis:
    """读取即抛异常的 Redis 替身，用于验证读失败时回退到原始函数。"""

    async def get(self, key: str):
        raise ConnectionError("Redis 不可用（测试模拟）")

    async def set(self, *args, **kwargs):
        raise ConnectionError("Redis 不可用（测试模拟）")


class _ReadOnlyRedis:
    """可以读（始终未命中）但写入失败的 Redis 替身。"""

    async def get(self, key: str):
        return None

    async def set(self, *args, **kwargs):
        raise ConnectionError("Redis 写入失败（测试模拟）")


@pytest.fixture()
def cache_redis(monkeypatch: pytest.MonkeyPatch):
    """把 cache 插件的模块级 Redis 客户端指向内存 FakeRedis（退出时自动还原）。"""
    monkeypatch.setattr(cache_plugin, "_redis", fake_redis)
    fake_redis.reset()
    return fake_redis


# ---------- @cached ----------


def test_cached_second_call_hits_cache(cache_redis):
    """同一组参数第二次调用命中缓存，不再执行函数体。"""
    calls: list[int] = []

    @cache_plugin.cached("t", ttl=60)
    async def fn(x: int) -> dict:
        calls.append(x)
        return {"x": x}

    assert asyncio.run(fn(1)) == {"x": 1}
    assert asyncio.run(fn(1)) == {"x": 1}
    assert calls == [1]
    assert len(cache_redis._data) == 1


def test_cached_different_args_use_different_keys(cache_redis):
    """不同参数生成不同缓存键，互不干扰。"""
    calls: list[int] = []

    @cache_plugin.cached("t", ttl=60)
    async def fn(x: int) -> int:
        calls.append(x)
        return x * 10

    assert asyncio.run(fn(1)) == 10
    assert asyncio.run(fn(2)) == 20
    assert asyncio.run(fn(1)) == 10
    assert sorted(calls) == [1, 2]
    assert len(cache_redis._data) == 2


def test_cached_without_redis_calls_function_directly(monkeypatch: pytest.MonkeyPatch):
    """Redis 未初始化时直接调用原函数（不缓存、不报错）。"""
    monkeypatch.setattr(cache_plugin, "_redis", None)
    calls: list[int] = []

    @cache_plugin.cached("t")
    async def fn() -> str:
        calls.append(1)
        return "v"

    assert asyncio.run(fn()) == "v"
    assert asyncio.run(fn()) == "v"
    assert len(calls) == 2


def test_cached_falls_back_when_redis_read_fails(monkeypatch: pytest.MonkeyPatch):
    """读缓存异常时回退到原函数，不影响请求。"""
    monkeypatch.setattr(cache_plugin, "_redis", _BrokenRedis())

    @cache_plugin.cached("t")
    async def fn() -> str:
        return "v"

    assert asyncio.run(fn()) == "v"


def test_cached_returns_value_when_redis_write_fails(monkeypatch: pytest.MonkeyPatch):
    """写缓存异常时仍返回计算结果，仅记日志。"""
    monkeypatch.setattr(cache_plugin, "_redis", _ReadOnlyRedis())

    @cache_plugin.cached("t")
    async def fn() -> str:
        return "v"

    assert asyncio.run(fn()) == "v"


# ---------- invalidate ----------


def test_invalidate_removes_only_matching_prefix(cache_redis):
    """invalidate 只删除 cache:{prefix}:* 的键，返回删除数量。"""

    async def seed() -> None:
        await cache_redis.set("cache:user:a", "1")
        await cache_redis.set("cache:user:b", "2")
        await cache_redis.set("cache:member:c", "3")

    asyncio.run(seed())
    assert asyncio.run(cache_plugin.invalidate("user")) == 2
    assert "cache:member:c" in cache_redis._data


def test_invalidate_without_redis_returns_zero(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cache_plugin, "_redis", None)
    assert asyncio.run(cache_plugin.invalidate("user")) == 0


def test_write_then_invalidate_clears_cached_value(cache_redis):
    """典型用法：读命中缓存 → 写操作后 invalidate → 再读重新回源。"""
    calls: list[int] = []

    @cache_plugin.cached("user", ttl=300)
    async def read_user(uid: int) -> dict:
        calls.append(uid)
        return {"id": uid, "rev": len(calls)}

    assert asyncio.run(read_user(1))["rev"] == 1
    assert asyncio.run(read_user(1))["rev"] == 1  # 命中缓存
    assert asyncio.run(cache_plugin.invalidate("user")) == 1
    assert asyncio.run(read_user(1))["rev"] == 2  # 缓存已清，重新回源


# ---------- set_redis / get_redis ----------


def test_get_redis_raises_when_not_initialized(monkeypatch: pytest.MonkeyPatch):
    """未初始化时 get_redis 抛 RuntimeError，提示需先 set_redis。"""
    monkeypatch.setattr(cache_plugin, "_redis", None)
    with pytest.raises(RuntimeError):
        cache_plugin.get_redis()


def test_set_redis_injects_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cache_plugin, "_redis", None)
    cache_plugin.set_redis(cast("Redis", fake_redis))
    assert cache_plugin.get_redis() is fake_redis
    monkeypatch.setattr(cache_plugin, "_redis", None)
