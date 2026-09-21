from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import result
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.result import Result
from app.models import User
from app.schemas.user import CreateUser, UserOut
from app.services import user as user_service

router = APIRouter(prefix="/user", tags=["用户"])


@router.get("/get_user", summary="通过用户id获取用户", response_model=Result[UserOut])
def get_user_by_id(user_id: int, db: Session = Depends(get_db)):
    user = user_service.get_user_by_id(db, user_id)
    if user is None:
        return result.ok(data=None, message="没有该用户")
    return result.ok(data=UserOut.model_validate(user))


@router.post("/create_user", summary="创建用户", response_model=Result[UserOut])
def create_user(user: CreateUser, db: Session = Depends(get_db)):
    created = user_service.create_user(db, user)
    if created is None:
        raise HTTPException(status_code=409, detail="邮箱已被注册")
    return result.ok(data=UserOut.model_validate(created), message="添加成功")


@router.get("/me", summary="获取当前登录用户", response_model=Result[UserOut])
def get_me(current_user: User = Depends(get_current_user)):
    return result.ok(data=UserOut.model_validate(current_user))
