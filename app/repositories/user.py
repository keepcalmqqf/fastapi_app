from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.base import utc_now
from app.schemas.user import CreateUser


async def count_users(db: AsyncSession) -> int:
    # 软删除的记录不计入（空库自举判断只看有效用户）
    result = await db.scalar(
        select(func.count()).select_from(User).where(User.deleted_at.is_(None))
    )
    return result or 0


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))


async def list_users(db: AsyncSession, page: int, page_size: int) -> tuple[list[User], int]:
    """分页查询有效用户，按 id 升序；返回 (items, total)。"""
    total = await count_users(db)
    result = await db.scalars(
        select(User)
        .where(User.deleted_at.is_(None))
        .order_by(User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.all()), total


async def create_user(db: AsyncSession, data: CreateUser, hashed_password: str) -> User:
    user = User(
        name=data.name,
        email=data.email,
        password=hashed_password,
        is_active=data.is_active,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def update_user(db: AsyncSession, user: User, data: dict) -> User:
    """只更新 data 中非 None 的字段。事务边界在 service 层。"""
    for key, value in data.items():
        if value is not None:
            setattr(user, key, value)
    await db.flush()
    # 显式刷新：确保返回值带上数据库侧最终落定的字段（server_default / onupdate 兜底），
    # 同时避免 async 下惰性刷新抛 MissingGreenlet
    await db.refresh(user)
    return user


async def soft_delete_user(db: AsyncSession, user: User) -> None:
    user.deleted_at = utc_now()
    await db.flush()
