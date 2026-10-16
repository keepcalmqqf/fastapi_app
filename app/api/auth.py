from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import result
from app.core.database import get_db
from app.core.deps import get_current_user, get_redis
from app.core.result import Result
from app.core.revocation import revoke_subject
from app.core.settings import settings
from app.login_guard import login_guard
from app.models import User
from app.schemas.rbac import UserPermissionsOut
from app.schemas.token import TokenOut
from app.schemas.user import ChangePassword, CreateUser, UserLogin, UserOut
from app.services import audit as audit_service
from app.services import rbac as rbac_service
from app.services import tokens as token_service
from app.services import user as user_service
from app.services.audit import ACTION_CHANGE_PASSWORD, ACTION_CREATE_USER, ACTION_LOGIN_FAILED

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/bootstrap", summary="初始化首个管理员（空库自举）", response_model=Result[UserOut])
async def bootstrap(
    data: CreateUser,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Result[UserOut]:
    """空库且 ALLOW_BOOTSTRAP 打开时创建首个管理员，并授予超级管理员角色。

    与 POST /api/v1/user/ 分离的原因：后者是「管理员账号管理」能力，恒需鉴权；
    合在一个端点会让空库状态下的创建用户退化为免鉴权入口，任何人抢先调用即可
    成为超管。前端可先查 /api/v1/info 的 needs_bootstrap 再决定是否展示该入口。
    """
    if not settings.bootstrap_allowed:
        raise HTTPException(status_code=403, detail="管理员初始化已关闭")
    # 先判空库再创建：与创建动作在同一连接上顺序执行，缩小并发抢建窗口
    if not await user_service.needs_bootstrap(db):
        raise HTTPException(status_code=409, detail="管理员已初始化")
    created = await user_service.create_user(db, data)
    if created is None:
        raise HTTPException(status_code=409, detail="邮箱已被注册")
    await rbac_service.ensure_seeded(db)
    await rbac_service.assign_role_to_user(db, created, rbac_service.SUPER_ADMIN_CODE)
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=None,  # 自举场景尚无操作者
        action=ACTION_CREATE_USER,
        target_type="user",
        target_id=str(created.id),
        detail={"email": created.email, "bootstrap": True},
        ip=audit_service.client_ip(request),
    )
    return result.ok(data=UserOut.model_validate(created), message="管理员初始化成功")


@router.get("/permissions", summary="当前登录用户权限", response_model=Result[UserPermissionsOut])
async def my_permissions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Result[UserPermissionsOut]:
    """返回当前用户拥有的角色 code 与权限 code，供前端菜单/按钮级鉴权。"""
    role_codes = await rbac_service.get_user_role_codes(db, current_user.id)
    perm_codes = await rbac_service.get_user_permission_codes(db, current_user.id)
    return result.ok(
        data=UserPermissionsOut(role_codes=sorted(role_codes), permission_codes=sorted(perm_codes)),
        message="请求成功",
    )


class RefreshBody(BaseModel):
    """刷新令牌兜底请求体（优先使用 refresh_token Cookie）。"""

    refresh_token: str | None = None


@router.post("/login", summary="登录并获取访问令牌", response_model=Result[TokenOut])
async def login(
    data: UserLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Any = Depends(get_redis),
) -> Result[TokenOut]:
    ip = audit_service.client_ip(request)
    # 防爆破：连续失败达阈值后按「账号 + IP」锁定，先查锁再校验密码
    await login_guard.ensure_not_locked(redis, subject=f"admin:{data.email.lower()}", ip=ip)
    user = await user_service.authenticate(db, data.email, data.password)
    if user is None:
        # 登录失败要留痕：连续失败是撞库/爆破的信号（限流仅按 IP 计数，审计可按时段复盘）
        await audit_service.record(
            db,
            actor_type="system",
            actor_id=None,
            action=ACTION_LOGIN_FAILED,
            target_type="user",
            target_id=data.email,
            ip=ip,
        )
        await login_guard.register_failure(redis, subject=f"admin:{data.email.lower()}", ip=ip)
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    await login_guard.reset(redis, subject=f"admin:{data.email.lower()}", ip=ip)
    if settings.REQUIRE_EMAIL_VERIFICATION and not user.email_verified:
        raise HTTPException(status_code=403, detail="邮箱未验证，请先完成邮箱验证")
    token_out = await token_service.issue_tokens(str(user.id), "admin", response, redis)
    return result.ok(data=token_out, message="登录成功", translate=True)


@router.post("/refresh", summary="刷新访问令牌", response_model=Result[TokenOut])
async def refresh(
    request: Request,
    response: Response,
    body: RefreshBody | None = None,
    redis: Any = Depends(get_redis),
) -> Result[TokenOut]:
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token and body is not None:
        refresh_token = body.refresh_token
    if not refresh_token:
        raise HTTPException(status_code=401, detail="未提供刷新令牌")
    token_out = await token_service.rotate_refresh_token(refresh_token, response, redis)
    return result.ok(data=token_out, message="令牌已刷新", translate=True)


@router.post("/logout", summary="登出并撤销令牌", response_model=Result[None])
async def logout(
    request: Request, response: Response, redis: Any = Depends(get_redis)
) -> Result[None]:
    # access 令牌：Authorization Bearer 优先，其次 access_token Cookie
    access_token = request.cookies.get("access_token")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        access_token = auth[7:].strip() or access_token
    refresh_token = request.cookies.get("refresh_token")
    await token_service.revoke_tokens(access_token, refresh_token, redis)
    # path 需与写入时一致，否则浏览器删不掉 Cookie
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return result.ok(message="已退出登录", translate=True)


@router.post("/change-password", summary="修改当前登录用户密码", response_model=Result[None])
async def change_password(
    data: ChangePassword,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Any = Depends(get_redis),
) -> Result[None]:
    changed = await user_service.change_password(
        db, current_user, data.old_password, data.new_password
    )
    if not changed:
        raise HTTPException(status_code=400, detail="原密码不正确")
    # 凭证已变更：终止该账号的全部会话（含其他设备的登录态）。
    # 撤销按签发时间判定，当前令牌同样失效，符合「改密后重新登录」的通行做法。
    await revoke_subject("admin", str(current_user.id), redis)
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=current_user.id,
        action=ACTION_CHANGE_PASSWORD,
        target_type="user",
        target_id=str(current_user.id),
        ip=audit_service.client_ip(request),
    )
    return result.ok(message="密码已修改，请重新登录", translate=True)
