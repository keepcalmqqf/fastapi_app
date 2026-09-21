from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from config.database import get_db
from domain.user import CreateUser, UserOut
from service import user_service
from util import result

router = APIRouter(prefix="/user", tags=["用户"])


@router.get("/get_user", summary="通过用户id获取用户")
def get_user_by_id(user_id: int, db: Session = Depends(get_db)):
    user = user_service.get_user_by_id(db, user_id)
    if user is None:
        return result.ok(data=None, message='没有该用户')
    return result.ok(data=UserOut.model_validate(user).model_dump())


@router.post("/create_user", summary="创建用户")
def create_user(user: CreateUser, db: Session = Depends(get_db)):
    user = user_service.create_user(db, user)
    return result.ok(data=UserOut.model_validate(user).model_dump(), message="添加成功")
