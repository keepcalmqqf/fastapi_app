from sqlalchemy.orm import Session

from config.models import User
from domain.user import CreateUser


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, data: CreateUser) -> User:
    user = User(name=data.name, email=data.email, password=data.password,
                is_active=data.is_active)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
