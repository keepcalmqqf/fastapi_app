from sqlalchemy.orm import Session

from app.core import security
from app.models import Member
from app.repositories import member as member_repo
from app.schemas.member import RegisterMember


def register(db: Session, data: RegisterMember) -> Member | None:
    """注册会员；邮箱已存在时返回 None。事务边界在本层。"""
    if member_repo.get_member_by_email(db, data.email) is not None:
        return None
    member = member_repo.create_member(db, data, security.hash_password(data.password))
    db.commit()
    return member


def authenticate(db: Session, email: str, password: str) -> Member | None:
    member = member_repo.get_member_by_email(db, email)
    if member is None or member.password is None:
        return None
    if not security.verify_password(password, member.password):
        return None
    return member
