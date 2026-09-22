from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.models import User
from app.repositories import user as user_repo
from app.schemas.user import CreateUser

# 预置的 dummy 哈希：用户不存在时也执行一次 Argon2 verify，拉平登录耗时，
# 避免通过响应时间差枚举已注册邮箱。
_DUMMY_PASSWORD_HASH = security.hash_password("dummy-password-for-timing-equalization")


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return user_repo.get_user_by_id(db, user_id)


def needs_bootstrap(db: Session) -> bool:
    """users 表是否为空（空库时允许无凭证创建首个管理员）。"""
    return user_repo.count_users(db) == 0


def create_user(db: Session, data: CreateUser) -> User | None:
    """创建用户；邮箱已存在（含并发撞唯一约束）时返回 None。事务边界在本层。"""
    if user_repo.get_user_by_email(db, data.email) is not None:
        return None
    user = user_repo.create_user(db, data, security.hash_password(data.password))
    try:
        db.commit()
    except IntegrityError:
        # 并发注册撞唯一约束：回滚并降级为「已存在」，由 api 层转为 409
        db.rollback()
        return None
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = user_repo.get_user_by_email(db, email)
    # 用户不存在或无密码时改验 dummy 哈希，保证恒有一次 Argon2 计算
    hashed = user.password if user is not None and user.password else _DUMMY_PASSWORD_HASH
    if not security.verify_password(password, hashed):
        return None
    # 密码错误与账号禁用返回同样的 None，避免泄露账号状态
    if user is None or not user.is_active:
        return None
    return user
