from sqlalchemy.orm import Session

from app.core import security
from app.models import User
from app.repositories import user as user_repo
from app.schemas.user import CreateUser


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return user_repo.get_user_by_id(db, user_id)


def create_user(db: Session, data: CreateUser) -> User | None:
    """创建用户；邮箱已存在时返回 None。事务边界在本层。"""
    if user_repo.get_user_by_email(db, data.email) is not None:
        return None
    user = user_repo.create_user(db, data, security.hash_password(data.password))
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = user_repo.get_user_by_email(db, email)
    if user is None or user.password is None:
        return None
    if not security.verify_password(password, user.password):
        return None
    return user
