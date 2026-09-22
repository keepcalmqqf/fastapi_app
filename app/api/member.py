from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core import result
from app.core.database import get_db
from app.core.deps import get_current_member, get_current_user
from app.core.result import Result
from app.models import Member, User
from app.schemas.member import (
    MemberLogin,
    MemberOut,
    RefreshTokenIn,
    RegisterMember,
    UpdateMember,
)
from app.schemas.page import Page, PageParams
from app.schemas.token import TokenOut
from app.services import member as member_service
from app.services import tokens as token_service

router = APIRouter(prefix="/member", tags=["会员"])


@router.post("/register", summary="会员注册", response_model=Result[MemberOut])
def register(data: RegisterMember, db: Session = Depends(get_db)):
    member = member_service.register(db, data)
    if member is None:
        raise HTTPException(status_code=409, detail="邮箱已被注册")
    return result.ok(data=MemberOut.model_validate(member), message="注册成功")


@router.post("/login", summary="会员登录并签发令牌", response_model=Result[TokenOut])
async def login(
    data: MemberLogin,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    member = member_service.authenticate(db, data.email, data.password)
    if member is None:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    tokens = await token_service.issue_tokens(
        subject=str(member.id), aud="member", response=response, redis=request.app.state.redis
    )
    return result.ok(data=tokens, message="登录成功")


@router.post("/refresh", summary="刷新令牌", response_model=Result[TokenOut])
async def refresh(
    request: Request,
    response: Response,
    data: RefreshTokenIn | None = None,
):
    # cookie 优先，body 中的 refresh_token 兜底
    refresh_token = request.cookies.get("refresh_token") or (data.refresh_token if data else None)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="缺少 refresh token")
    tokens = await token_service.rotate_refresh_token(
        refresh_token=refresh_token, response=response, redis=request.app.state.redis
    )
    return result.ok(data=tokens, message="刷新成功")


@router.post("/logout", summary="退出登录并撤销令牌", response_model=Result[None])
async def logout(request: Request, response: Response):
    # access token 优先取 cookie，缺失时回退 Authorization 头；取不到则跳过撤销
    access_token = request.cookies.get("access_token")
    if not access_token:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            access_token = authorization[7:]
    refresh_token = request.cookies.get("refresh_token")
    await token_service.revoke_tokens(
        access_token=access_token, refresh_token=refresh_token, redis=request.app.state.redis
    )
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return result.ok(message="退出登录成功")


@router.get("/me", summary="获取当前登录会员", response_model=Result[MemberOut])
def get_me(current_member: Member = Depends(get_current_member)):
    return result.ok(data=MemberOut.model_validate(current_member))


@router.patch("/me", summary="更新当前登录会员", response_model=Result[MemberOut])
def update_me(
    data: UpdateMember,
    db: Session = Depends(get_db),
    current_member: Member = Depends(get_current_member),
):
    updated = member_service.update_member(db, current_member, data.model_dump(exclude_unset=True))
    return result.ok(data=MemberOut.model_validate(updated), message="更新成功")


@router.delete("/me", summary="注销当前登录会员（软删除）", response_model=Result[None])
async def delete_me(
    request: Request,
    db: Session = Depends(get_db),
    current_member: Member = Depends(get_current_member),
):
    member_service.delete_member(db, current_member)
    # 软删除后顺手撤销其令牌：access 取 cookie/header，refresh 取 cookie，取不到就跳过
    access_token = request.cookies.get("access_token")
    if not access_token:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            access_token = authorization[7:]
    refresh_token = request.cookies.get("refresh_token")
    await token_service.revoke_tokens(
        access_token=access_token, refresh_token=refresh_token, redis=request.app.state.redis
    )
    return result.ok(message="注销成功")


# 会员列表属于后台运营能力，与会员自身接口不同：这里用后台管理员令牌鉴权
# （get_current_user），而非会员令牌（get_current_member）。
@router.get("/list", summary="分页获取会员列表（后台）", response_model=Result[Page[MemberOut]])
def list_members(
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    items, total = member_service.list_members(db, params.page, params.page_size)
    page = Page[MemberOut](
        total=total,
        page=params.page,
        page_size=params.page_size,
        items=[MemberOut.model_validate(m) for m in items],
    )
    return result.ok(data=page)
