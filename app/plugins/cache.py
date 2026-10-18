import functools
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger("fastapi_app")

_redis: Redis | None = None


def set_redis(client: Redis) -> None:
    """lifespan 中调用，注入 Redis 客户端。"""
    global _redis
    _redis = client


def get_redis() -> Redis:
    """取 lifespan 注入的 Redis 客户端；未注入时抛异常。

    只供「Redis 是必需依赖」的调用方使用。旁路用途（缓存失效、缓存清理）请用
    `get_redis_or_none()`：那类调用不能因为 Redis 没准备好就让请求失败。
    """
    if _redis is None:
        raise RuntimeError("Redis 未初始化，请先在 lifespan 中调用 set_redis()")
    return _redis


def get_redis_or_none() -> Redis | None:
    """旁路用途：Redis 未初始化时返回 None，不抛异常。

    worker 进程与定时任务不会走 lifespan 注入，而权限缓存失效又总是发生在
    `db.commit()` 之后——用 `get_redis()` 时 RuntimeError 会让「数据已改、接口
    却 500」，缓存这种旁路不该决定请求的成败。
    """
    return _redis


def _build_key(prefix: str, func_name: str, args: tuple, kwargs: dict) -> str:
    """用严格 JSON 构造缓存键；参数不可序列化时抛 TypeError。

    此前用 `default=str` 兜底：ORM 对象、`AsyncSession`、`Request` 会被转成
    `<...object at 0x7f9c...>`，**内存地址进了哈希输入** —— 任何带 `db=` /
    `request=` 参数的被装饰函数每次调用拿到的 key 都不同，缓存永不命中，却照样
    付一遍 json.dumps + sha256 的开销。这里改为严格序列化，让不可缓存的调用
    直接暴露出来（由 `cached` 降级为「不缓存、直接调用」）。

    键里带上函数名：只由 prefix + 入参决定时，两个共用同一 prefix 的函数在
    入参相同的情况下会互相串数据。
    """
    raw = json.dumps([func_name, args, kwargs], sort_keys=True)
    return f"cache:{prefix}:{hashlib.sha256(raw.encode()).hexdigest()}"


# 可无损往返 JSON 的返回类型：缓存命中时返回 json.loads 的结果，类型必须与
# 未命中时一致。set/datetime/Model 等虽能被 default=str 塞进去，还原出来却
# 变成 list/str/dict —— 那类返回值一律不缓存，宁可没缓存也不能让命中与未命中
# 行为不同（这种 bug 只在缓存命中的那一刻才出现，极难复现）。
_CACHEABLE = (dict, list, str, int, float, bool)


def _is_cacheable(value: Any) -> bool:
    return value is None or isinstance(value, _CACHEABLE)


async def invalidate(prefix: str) -> int:
    """删除指定前缀的所有缓存键（cache:{prefix}:*），返回删除数量。

    写操作（新增/更新/删除）修改数据后应调用本函数清理对应前缀的缓存，
    避免后续读取命中旧值。
    """
    if _redis is None:
        return 0
    count = 0
    async for key in _redis.scan_iter(f"cache:{prefix}:*"):
        await _redis.delete(key)
        count += 1
    return count


def cached(prefix: str, ttl: int = 60) -> Callable:
    """异步函数结果缓存装饰器（缓存其 JSON 原生类型的返回值）。

    用法:
        @cached("user", ttl=300)
        async def get_user(user_id: int): ...

    限制（违反时自动降级为「不缓存」，不会出错，只是没了加速）：
    - 入参必须是 JSON 可序列化的。传 `db=`/`request=` 这类对象会建不出稳定的键
      （对象地址每次都变），此时本装饰器直接调用原函数而不缓存。
    - 返回值必须是 dict/list/str/int/float/bool/None。set、datetime、Pydantic
      Model 等一律不缓存——命中时返回的是 json.loads 的结果，与未命中时的原始
      对象类型不一致，那类 bug 只在命中那一刻才暴露。

    写操作后应调用 invalidate("user") 清理缓存。
    """

    def decorator(func: Callable) -> Callable:
        func_name = f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if _redis is None:
                return await func(*args, **kwargs)
            try:
                key = _build_key(prefix, func_name, args, kwargs)
            except (TypeError, ValueError):
                logger.warning(
                    "缓存键无法构造（入参不可 JSON 序列化），本次不缓存: %s",
                    func_name,
                )
                return await func(*args, **kwargs)
            try:
                hit = await _redis.get(key)
                if hit is not None:
                    return json.loads(hit)
            except Exception:
                logger.warning("缓存读取失败，回退到原始函数", exc_info=True)
                return await func(*args, **kwargs)
            value = await func(*args, **kwargs)
            if not _is_cacheable(value):
                return value
            try:
                await _redis.set(key, json.dumps(value), ex=ttl)
            except Exception:
                logger.warning("缓存写入失败", exc_info=True)
            return value

        return wrapper

    return decorator
