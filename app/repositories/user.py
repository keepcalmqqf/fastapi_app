from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User
from app.schemas.user import CreateUser


def count_users(db: Session) -> int:
    # 软删除的记录不计入（空库自举判断只看有效用户）
    return db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None))) or 0


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))


def list_users(db: Session, page: int, page_size: int) -> tuple[list[User], int]:
    """分页查询有效用户，按 id 升序；返回 (items, total)。"""
    total = count_users(db)
    items = db.scalars(
        select(User)
        .where(User.deleted_at.is_(None))
        .order_by(User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(items), total


def create_user(db: Session, data: CreateUser, hashed_password: str) -> User:
    user = User(
        name=data.name,
        email=data.email,
        password=hashed_password,
        is_active=data.is_active,
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    return user


def update_user(db: Session, user: User, data: dict) -> User:
    """只更新 data 中非 None 的字段。事务边界在 service 层。"""
    for key, value in data.items():
        if value is not None:
            setattr(user, key, value)
    db.flush()
    return user


def soft_delete_user(db: Session, user: User) -> None:
    user.deleted_at = datetime.now(timezone.utc)
    db.flush()
