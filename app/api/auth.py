from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import result
from app.core.database import get_db
from app.core.result import Result
from app.core.security import create_access_token
from app.schemas.user import Token, UserLogin
from app.services import user as user_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", summary="登录并获取访问令牌", response_model=Result[Token])
def login(data: UserLogin, db: Session = Depends(get_db)):
    user = user_service.authenticate(db, data.email, data.password)
    if user is None:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token = create_access_token(subject=str(user.id))
    return result.ok(data=Token(access_token=token), message="登录成功")
