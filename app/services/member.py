from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.models import Member
from app.repositories import member as member_repo
from app.schemas.member import RegisterMember

# 预置的 dummy 哈希：会员不存在时也执行一次 Argon2 verify，拉平登录耗时，
# 避免通过响应时间差枚举已注册邮箱。
_DUMMY_PASSWORD_HASH = security.hash_password("dummy-password-for-timing-equalization")


def register(db: Session, data: RegisterMember) -> Member | None:
    """注册会员；邮箱已存在（含并发撞唯一约束）时返回 None。事务边界在本层。"""
    if member_repo.get_member_by_email(db, data.email) is not None:
        return None
    member = member_repo.create_member(db, data, security.hash_password(data.password))
    try:
        db.commit()
    except IntegrityError:
        # 并发注册撞唯一约束：回滚并降级为「已存在」，由 api 层转为 409
        db.rollback()
        return None
    return member


def authenticate(db: Session, email: str, password: str) -> Member | None:
    member = member_repo.get_member_by_email(db, email)
    # 会员不存在或无密码时改验 dummy 哈希，保证恒有一次 Argon2 计算
    hashed = member.password if member is not None and member.password else _DUMMY_PASSWORD_HASH
    if not security.verify_password(password, hashed):
        return None
    # 密码错误与账号禁用返回同样的 None，避免泄露账号状态
    if member is None or not member.is_active:
        return None
    return member
