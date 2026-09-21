import functools
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
    if _redis is None:
        raise RuntimeError("Redis 未初始化，请先在 lifespan 中调用 set_redis()")
    return _redis


def cached(prefix: str, ttl: int = 60) -> Callable:
    """异步函数结果缓存装饰器（缓存其 JSON 可序列化的返回值）。

    用法:
        @cached("user", ttl=300)
        async def get_user(user_id: int): ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if _redis is None:
                return await func(*args, **kwargs)
            key = f"cache:{prefix}:{json.dumps([args, kwargs], default=str, sort_keys=True)}"
            try:
                hit = await _redis.get(key)
                if hit is not None:
                    return json.loads(hit)
            except Exception:
                logger.warning("缓存读取失败，回退到原始函数", exc_info=True)
                return await func(*args, **kwargs)
            value = await func(*args, **kwargs)
            try:
                await _redis.set(key, json.dumps(value, default=str), ex=ttl)
            except Exception:
                logger.warning("缓存写入失败", exc_info=True)
            return value

        return wrapper

    return decorator
