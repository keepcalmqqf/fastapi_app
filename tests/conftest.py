import asyncio
import fnmatch
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TypeVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import health as health_module
from app.core.database import get_db
from app.main import app
from app.models import Base

async_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = async_sessionmaker(
    autocommit=False, autoflush=False, expire_on_commit=False, bind=async_engine
)


async def _create_tables() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


asyncio.run(_create_tables())

# /health 直接使用 app.core.database 的引擎（不经 get_db 依赖），替换为测试库保证 mysql 组件恒为 ok
health_module.async_engine = async_engine


async def override_get_db() -> AsyncIterator[AsyncSession]:
    async with TestingSessionLocal() as db:
        yield db


app.dependency_overrides[get_db] = override_get_db

T = TypeVar("T")


def run_db(fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """同步执行一段需要 AsyncSession 的异步操作（测试断言/造数用）。

    测试本身是同步函数，而应用层已全面异步化；这里用 asyncio.run 起一个
    一次性事件循环执行数据库操作。StaticPool 保证与应用共享同一个内存库连接。
    """

    async def _run() -> T:
        async with TestingSessionLocal() as db:
            return await fn(db)

    return asyncio.run(_run())


class FakeRedis:
    """内存版 Redis 客户端，实现测试链路实际用到的接口。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        # 故障开关：置 True 后 ping 抛异常，供 /health degraded 用例使用
        self.broken = False

    def reset(self) -> None:
        self._data.clear()
        self.broken = False

    async def ping(self) -> bool:
        if self.broken:
            raise ConnectionError("Redis 连接失败（测试模拟）")
        return True

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._data[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        return await self.set(key, value, ex=ttl)

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
        return count

    def scan_iter(self, pattern: str):
        async def _gen():
            for key in list(self._data):
                if fnmatch.fnmatch(key, pattern):
                    yield key

        return _gen()

    async def aclose(self) -> None:
        pass


fake_redis = FakeRedis()


@pytest.fixture(autouse=True)
def isolate_redis(monkeypatch: pytest.MonkeyPatch):
    """用 FakeRedis 替换 lifespan 中注入的 Redis 客户端，避免测试连接真实 Redis。"""
    fake_redis.reset()
    monkeypatch.setattr("app.main.create_redis", lambda: fake_redis)
    yield


@pytest.fixture()
def broken_redis() -> Iterator[FakeRedis]:
    """将 Redis 置为故障态，供 /health degraded 用例使用。"""
    fake_redis.broken = True
    yield fake_redis
    fake_redis.broken = False


@pytest.fixture(autouse=True)
def clean_tables():
    yield

    async def _clean() -> None:
        async with TestingSessionLocal() as db:
            for table in reversed(Base.metadata.sorted_tables):
                await db.execute(table.delete())
            await db.commit()

    asyncio.run(_clean())


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
