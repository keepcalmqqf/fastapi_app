from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.schemas.user import CreateUser


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.scalar(select(User).where(User.id == user_id))


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


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
