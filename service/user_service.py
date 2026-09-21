from sqlalchemy.orm import Session

from config.models import User
from crud import user_crud
from domain.user import CreateUser


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return user_crud.get_user_by_id(db, user_id)


def create_user(db: Session, data: CreateUser) -> User:
    return user_crud.create_user(db, data)
