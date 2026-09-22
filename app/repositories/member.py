from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Member
from app.schemas.member import RegisterMember


def get_member_by_id(db: Session, member_id: int) -> Member | None:
    return db.scalar(select(Member).where(Member.id == member_id, Member.deleted_at.is_(None)))


def get_member_by_email(db: Session, email: str) -> Member | None:
    return db.scalar(select(Member).where(Member.email == email, Member.deleted_at.is_(None)))


def count_members(db: Session) -> int:
    return (
        db.scalar(select(func.count()).select_from(Member).where(Member.deleted_at.is_(None))) or 0
    )


def list_members(db: Session, page: int, page_size: int) -> tuple[list[Member], int]:
    """分页查询有效会员，按 id 升序；返回 (items, total)。"""
    total = count_members(db)
    items = db.scalars(
        select(Member)
        .where(Member.deleted_at.is_(None))
        .order_by(Member.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(items), total


def create_member(db: Session, data: RegisterMember, hashed_password: str) -> Member:
    member = Member(
        nickname=data.nickname,
        email=data.email,
        password=hashed_password,
    )
    db.add(member)
    db.flush()
    db.refresh(member)
    return member


def update_member(db: Session, member: Member, data: dict) -> Member:
    """只更新 data 中非 None 的字段。事务边界在 service 层。"""
    for key, value in data.items():
        if value is not None:
            setattr(member, key, value)
    db.flush()
    return member


def soft_delete_member(db: Session, member: Member) -> None:
    member.deleted_at = datetime.now(timezone.utc)
    db.flush()
