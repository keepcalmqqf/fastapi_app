from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import result
from app.core.database import get_db
from app.core.deps import get_current_member
from app.core.result import Result
from app.core.security import create_access_token
from app.models import Member
from app.schemas.member import MemberLogin, MemberOut, RegisterMember
from app.schemas.user import Token
from app.services import member as member_service

router = APIRouter(prefix="/member", tags=["会员"])


@router.post("/register", summary="会员注册", response_model=Result[MemberOut])
def register(data: RegisterMember, db: Session = Depends(get_db)):
    member = member_service.register(db, data)
    if member is None:
        raise HTTPException(status_code=409, detail="邮箱已被注册")
    return result.ok(data=MemberOut.model_validate(member), message="注册成功")


@router.post("/login", summary="会员登录并获取访问令牌", response_model=Result[Token])
def login(data: MemberLogin, db: Session = Depends(get_db)):
    member = member_service.authenticate(db, data.email, data.password)
    if member is None:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token = create_access_token(subject=str(member.id), audience="member")
    return result.ok(data=Token(access_token=token), message="登录成功")


@router.get("/me", summary="获取当前登录会员", response_model=Result[MemberOut])
def get_me(current_member: Member = Depends(get_current_member)):
    return result.ok(data=MemberOut.model_validate(current_member))
