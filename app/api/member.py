from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import result
from app.core.database import get_db
from app.core.deps import get_current_member, get_redis
from app.core.rbac import require_permissions
from app.core.result import Result
from app.core.revocation import revoke_subject
from app.core.settings import settings
from app.models import Member
from app.repositories import member as member_repo
from app.schemas.member import (
    ChangeMemberPassword,
    MemberLogin,
    MemberOut,
    RefreshTokenIn,
    RegisterMember,
    UpdateMember,
)
from app.schemas.page import Page, PageParams
from app.schemas.token import TokenOut
from app.schemas.verify import VerifyEmailIn
from app.services import audit as audit_service
from app.services import email_verify as email_verify_service
from app.services import member as member_service
from app.services import tokens as token_service
from app.services.audit import (
    ACTION_CHANGE_MEMBER_PASSWORD,
    ACTION_DELETE_MEMBER,
    ACTION_LOGIN_FAILED,
)

router = APIRouter(prefix="/member", tags=["会员"])


@router.post("/register", summary="会员注册", response_model=Result[MemberOut])
async def register(data: RegisterMember, db: AsyncSession = Depends(get_db)) -> Result[MemberOut]:
    member = await member_service.register(db, data)
    if member is None:
        raise HTTPException(status_code=409, detail="邮箱已被注册")
    # 注册即投递验证邮件；投递失败不影响注册结果
    await email_verify_service.send_verification(
        aud="member", subject_id=member.id, email=member.email
    )
    return result.ok(data=MemberOut.model_validate(member), message="注册成功")


@router.post("/verify-email", summary="验证会员邮箱", response_model=Result[MemberOut])
async def verify_email(
    data: VerifyEmailIn, db: AsyncSession = Depends(get_db)
) -> Result[MemberOut]:
    member = await email_verify_service.verify(
        db, data.token, aud="member", loader=member_repo.get_member_by_id, label="会员"
    )
    return result.ok(data=MemberOut.model_validate(member), message="邮箱验证成功")


@router.post("/resend-verification", summary="重发会员验证邮件", response_model=Result[None])
async def resend_verification(current_member: Member = Depends(get_current_member)) -> Result[None]:
    if current_member.email_verified:
        return result.ok(message="邮箱已验证，无需重复发送")
    await email_verify_service.send_verification(
        aud="member", subject_id=current_member.id, email=current_member.email
    )
    return result.ok(message="验证邮件已发送")


@router.post("/login", summary="会员登录并签发令牌", response_model=Result[TokenOut])
async def login(
    data: MemberLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Any = Depends(get_redis),
) -> Result[TokenOut]:
    member = await member_service.authenticate(db, data.email, data.password)
    if member is None:
        await audit_service.record(
            db,
            actor_type="system",
            actor_id=None,
            action=ACTION_LOGIN_FAILED,
            target_type="member",
            target_id=data.email,
            ip=audit_service.client_ip(request),
        )
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    if settings.REQUIRE_EMAIL_VERIFICATION and not member.email_verified:
        raise HTTPException(status_code=403, detail="邮箱未验证，请先完成邮箱验证")
    tokens = await token_service.issue_tokens(
        subject=str(member.id), aud="member", response=response, redis=redis
    )
    return result.ok(data=tokens, message="登录成功")


@router.post("/refresh", summary="刷新令牌", response_model=Result[TokenOut])
async def refresh(
    request: Request,
    response: Response,
    data: RefreshTokenIn | None = None,
    redis: Any = Depends(get_redis),
) -> Result[TokenOut]:
    # cookie 优先，body 中的 refresh_token 兜底
    refresh_token = request.cookies.get("refresh_token") or (data.refresh_token if data else None)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="缺少 refresh token")
    tokens = await token_service.rotate_refresh_token(
        refresh_token=refresh_token, response=response, redis=redis
    )
    return result.ok(data=tokens, message="刷新成功")


@router.post("/logout", summary="退出登录并撤销令牌", response_model=Result[None])
async def logout(
    request: Request, response: Response, redis: Any = Depends(get_redis)
) -> Result[None]:
    # access token 优先取 cookie，缺失时回退 Authorization 头；取不到则跳过撤销
    access_token = request.cookies.get("access_token")
    if not access_token:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            access_token = authorization[7:]
    refresh_token = request.cookies.get("refresh_token")
    await token_service.revoke_tokens(
        access_token=access_token, refresh_token=refresh_token, redis=redis
    )
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return result.ok(message="退出登录成功")


@router.post("/change-password", summary="修改当前登录会员密码", response_model=Result[None])
async def change_password(
    data: ChangeMemberPassword,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_member: Member = Depends(get_current_member),
    redis: Any = Depends(get_redis),
) -> Result[None]:
    changed = await member_service.change_password(
        db, current_member, data.old_password, data.new_password
    )
    if not changed:
        raise HTTPException(status_code=401, detail="原密码不正确")
    # 与后台改密对称：终止该会员的全部会话（含其他设备）
    await revoke_subject("member", str(current_member.id), redis)
    await audit_service.record(
        db,
        actor_type="member",
        actor_id=current_member.id,
        action=ACTION_CHANGE_MEMBER_PASSWORD,
        target_type="member",
        target_id=str(current_member.id),
        ip=audit_service.client_ip(request),
    )
    return result.ok(message="密码已修改，请重新登录")


@router.get("/me", summary="获取当前登录会员", response_model=Result[MemberOut])
async def get_me(current_member: Member = Depends(get_current_member)) -> Result[MemberOut]:
    return result.ok(data=MemberOut.model_validate(current_member))


@router.patch("/me", summary="更新当前登录会员", response_model=Result[MemberOut])
async def update_me(
    data: UpdateMember,
    db: AsyncSession = Depends(get_db),
    current_member: Member = Depends(get_current_member),
) -> Result[MemberOut]:
    updated = await member_service.update_member(
        db, current_member, data.model_dump(exclude_unset=True)
    )
    return result.ok(data=MemberOut.model_validate(updated), message="更新成功")


@router.delete("/me", summary="注销当前登录会员（软删除）", response_model=Result[None])
async def delete_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_member: Member = Depends(get_current_member),
    redis: Any = Depends(get_redis),
) -> Result[None]:
    await member_service.delete_member(db, current_member)
    # 软删除后顺手撤销其令牌：access 取 cookie/header，refresh 取 cookie，取不到就跳过
    access_token = request.cookies.get("access_token")
    if not access_token:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            access_token = authorization[7:]
    refresh_token = request.cookies.get("refresh_token")
    await token_service.revoke_tokens(
        access_token=access_token, refresh_token=refresh_token, redis=redis
    )
    # 与后台软删用户对称：注销会员同样按主体撤销其全部令牌（覆盖其他设备的既有会话）
    await revoke_subject("member", str(current_member.id), redis)
    await audit_service.record(
        db,
        actor_type="member",
        actor_id=current_member.id,
        action=ACTION_DELETE_MEMBER,
        target_type="member",
        target_id=str(current_member.id),
        detail={"email": current_member.email},
        ip=audit_service.client_ip(request),
    )
    return result.ok(message="注销成功")


# 会员列表属于后台运营能力，与会员自身接口不同：这里用后台管理员令牌鉴权
# （get_current_user），而非会员令牌（get_current_member）。
@router.get("/", summary="分页获取会员列表（后台）", response_model=Result[Page[MemberOut]])
async def list_members(
    params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permissions("member:read")),
) -> Result[Page[MemberOut]]:
    items, total = await member_service.list_members(db, params.page, params.page_size)
    page = Page[MemberOut](
        total=total,
        page=params.page,
        page_size=params.page_size,
        items=[MemberOut.model_validate(m) for m in items],
    )
    return result.ok(data=page)
