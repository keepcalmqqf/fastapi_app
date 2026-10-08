from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import settings

async_engine = create_async_engine(
    settings.SQLALCHEMY_ASYNC_DATABASE_URL,
    echo=settings.MODE == "DEV",
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    autocommit=False,
    autoflush=False,
    # commit 后对象不过期：service 层 commit 之后，路由还要读取字段序列化响应，
    # 过期会触发额外的惰性刷新（async 下甚至报错）
    expire_on_commit=False,
    bind=async_engine,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as db:
        yield db
