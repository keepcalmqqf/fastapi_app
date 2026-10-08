from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Member
from app.schemas.member import RegisterMember


async def get_member_by_id(db: AsyncSession, member_id: int) -> Member | None:
    return await db.scalar(
        select(Member).where(Member.id == member_id, Member.deleted_at.is_(None))
    )


async def get_member_by_email(db: AsyncSession, email: str) -> Member | None:
    return await db.scalar(select(Member).where(Member.email == email, Member.deleted_at.is_(None)))


async def count_members(db: AsyncSession) -> int:
    result = await db.scalar(
        select(func.count()).select_from(Member).where(Member.deleted_at.is_(None))
    )
    return result or 0


async def list_members(db: AsyncSession, page: int, page_size: int) -> tuple[list[Member], int]:
    """分页查询有效会员，按 id 升序；返回 (items, total)。"""
    total = await count_members(db)
    result = await db.scalars(
        select(Member)
        .where(Member.deleted_at.is_(None))
        .order_by(Member.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.all()), total


async def create_member(db: AsyncSession, data: RegisterMember, hashed_password: str) -> Member:
    member = Member(
        nickname=data.nickname,
        email=data.email,
        password=hashed_password,
    )
    db.add(member)
    await db.flush()
    await db.refresh(member)
    return member


async def update_member(db: AsyncSession, member: Member, data: dict) -> Member:
    """只更新 data 中非 None 的字段。事务边界在 service 层。"""
    for key, value in data.items():
        if value is not None:
            setattr(member, key, value)
    await db.flush()
    # onupdate 列（updated_at）的新值由数据库生成，flush 后该属性处于过期状态，
    # async 下惰性刷新会抛 MissingGreenlet，这里显式刷新保证返回值可直接序列化
    await db.refresh(member)
    return member


async def soft_delete_member(db: AsyncSession, member: Member) -> None:
    member.deleted_at = datetime.now(timezone.utc)
    await db.flush()
