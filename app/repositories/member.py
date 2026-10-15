from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Member
from app.models.base import utc_now
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
    # 显式刷新：确保返回值带上数据库侧最终落定的字段（server_default / onupdate 兜底），
    # 同时避免 async 下惰性刷新抛 MissingGreenlet
    await db.refresh(member)
    return member


async def soft_delete_member(db: AsyncSession, member: Member) -> None:
    member.deleted_at = utc_now()
    await db.flush()
