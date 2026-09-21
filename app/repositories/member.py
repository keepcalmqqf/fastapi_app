from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member
from app.schemas.member import RegisterMember


def get_member_by_id(db: Session, member_id: int) -> Member | None:
    return db.scalar(select(Member).where(Member.id == member_id))


def get_member_by_email(db: Session, email: str) -> Member | None:
    return db.scalar(select(Member).where(Member.email == email))


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
