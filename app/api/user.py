from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import result
from app.core.database import get_db
from app.core.deps import bearer_scheme, get_current_user, get_redis
from app.core.rbac import assert_permissions, require_permissions
from app.core.result import Result
from app.core.revocation import revoke_subject
from app.core.settings import settings
from app.models import User
from app.repositories import rbac as rbac_repo
from app.repositories import user as user_repo
from app.schemas.page import Page, PageParams
from app.schemas.rbac import RoleOut, SetUserRolesIn
from app.schemas.user import CreateUser, UpdateUser, UserOut
from app.schemas.verify import VerifyEmailIn
from app.services import audit as audit_service
from app.services import email_verify as email_verify_service
from app.services import rbac as rbac_service
from app.services import user as user_service
from app.services.audit import (
    ACTION_CREATE_USER,
    ACTION_DELETE_USER,
    ACTION_UPDATE_USER,
)

router = APIRouter(prefix="/user", tags=["用户"])

# 路径统一为 REST 风格：集合用 /user/，单个资源用 /user/{user_id}。
# 早期的 RPC 风格路径（/user/list、/user/get_user、/user/create_user）已移除，
# 不再保留兼容别名。
# 注意注册顺序：/user/me 必须排在 /user/{user_id} 之前，否则会被路径参数
# 先行匹配并因无法转 int 而 422。


async def _load_user_or_404(db: AsyncSession, user_id: int) -> User:
    user = await user_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


async def _build_page(db: AsyncSession, params: PageParams) -> Page[UserOut]:
    items, total = await user_service.list_users(db, params.page, params.page_size)
    return Page[UserOut](
        total=total,
        page=params.page,
        page_size=params.page_size,
        items=[UserOut.model_validate(u) for u in items],
    )


async def _do_create(
    db: AsyncSession,
    data: CreateUser,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    redis: Any,
) -> UserOut:
    """创建用户；空库自举判断与鉴权都在这里，新旧路径共用。"""
    # 空库自举：开关打开且 users 表为空时，首个管理员可无凭证创建。
    # 开关由 ALLOW_BOOTSTRAP 控制（PROD 默认关闭）——否则服务首次启动后
    # 任何人抢先调用本接口即可成为管理员。
    # get_current_user 是 async 依赖，这里在端点内手动 await（传入 request 以支持
    # Cookie 来源令牌及其 CSRF 头校验，行为与依赖注入路径一致）。
    actor: User | None = None
    if not settings.bootstrap_allowed or not await user_service.needs_bootstrap(db):
        actor = await get_current_user(request=request, credentials=credentials, db=db, redis=redis)
        # 非自举场景：创建用户属于「管理用户」能力，需 user:write 权限
        await assert_permissions(db, actor.id, "user:write")
    created = await user_service.create_user(db, data)
    if created is None:
        raise HTTPException(status_code=409, detail="邮箱已被注册")
    # 空库自举：首个管理员自动初始化 RBAC 角色权限并授予超级管理员
    if actor is None:
        await rbac_service.ensure_seeded(db)
        await rbac_service.assign_role_to_user(db, created, rbac_service.SUPER_ADMIN_CODE)
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=actor.id if actor else None,  # 自举场景无操作者
        action=ACTION_CREATE_USER,
        target_type="user",
        target_id=str(created.id),
        detail={"email": created.email, "is_active": created.is_active},
        ip=audit_service.client_ip(request),
    )
    return UserOut.model_validate(created)


@router.post("/verify-email", summary="验证邮箱", response_model=Result[UserOut])
async def verify_email(data: VerifyEmailIn, db: AsyncSession = Depends(get_db)) -> Result[UserOut]:
    """凭验证邮件链接里的 token 标记邮箱已验证（公开接口，token 本身即凭证）。"""
    user = await email_verify_service.verify(
        db, data.token, aud="admin", loader=user_repo.get_user_by_id, label="用户"
    )
    return result.ok(data=UserOut.model_validate(user), message="邮箱验证成功")


@router.post("/resend-verification", summary="重发验证邮件", response_model=Result[None])
async def resend_verification(current_user: User = Depends(get_current_user)) -> Result[None]:
    if current_user.email_verified:
        return result.ok(message="邮箱已验证，无需重复发送")
    await email_verify_service.send_verification(
        aud="admin", subject_id=current_user.id, email=current_user.email
    )
    return result.ok(message="验证邮件已发送")


@router.get("/me", summary="获取当前登录用户", response_model=Result[UserOut])
async def get_me(current_user: User = Depends(get_current_user)) -> Result[UserOut]:
    return result.ok(data=UserOut.model_validate(current_user))


@router.get("/", summary="分页获取用户列表", response_model=Result[Page[UserOut]])
async def list_users(
    params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permissions("user:read")),
) -> Result[Page[UserOut]]:
    return result.ok(data=await _build_page(db, params))


@router.post("/", summary="创建用户", response_model=Result[UserOut])
async def create_user(
    user: CreateUser,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
    redis: Any = Depends(get_redis),
) -> Result[UserOut]:
    return result.ok(
        data=await _do_create(db, user, request, credentials, redis), message="添加成功"
    )


@router.get("/{user_id}", summary="通过用户 id 获取用户", response_model=Result[UserOut])
async def get_user_by_id(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permissions("user:read")),
) -> Result[UserOut]:
    return result.ok(data=UserOut.model_validate(await _load_user_or_404(db, user_id)))


@router.patch("/{user_id}", summary="更新用户", response_model=Result[UserOut])
async def update_user(
    user_id: int,
    data: UpdateUser,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permissions("user:write")),
    redis: Any = Depends(get_redis),
) -> Result[UserOut]:
    fields = data.model_dump(exclude_unset=True)
    # 不允许把自己停用：与「不允许删除自己」同理。停用后 authenticate 恒返回 None，
    # 若唯一管理员把自己停掉，全员再无任何入口恢复，只能直连数据库改行。
    if user_id == current_user.id and fields.get("is_active") is False:
        raise HTTPException(status_code=400, detail="不允许停用自己的账号")
    user = await _load_user_or_404(db, user_id)
    updated = await user_service.update_user(db, user, fields)
    # 停用账号 = 终止其全部会话：撤销该主体所有已签发令牌，避免旧令牌在有效期内继续可用。
    # 重新启用不会复活旧会话（撤销标记按签发时间判定），用户重新登录即可获得新令牌。
    if fields.get("is_active") is False:
        await revoke_subject("admin", str(user_id), redis)
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=current_user.id,
        action=ACTION_UPDATE_USER,
        target_type="user",
        target_id=str(user_id),
        detail=fields,
        ip=audit_service.client_ip(request),
    )
    return result.ok(data=UserOut.model_validate(updated), message="更新成功")


@router.delete("/{user_id}", summary="删除用户（软删除）", response_model=Result[None])
async def delete_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permissions("user:write")),
    redis: Any = Depends(get_redis),
) -> Result[None]:
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不允许删除当前登录用户")
    user = await _load_user_or_404(db, user_id)
    await user_service.delete_user(db, user)
    # 软删除后撤销该账号全部令牌；软删不释放邮箱唯一约束，该账号不会再产生新会话
    await revoke_subject("admin", str(user_id), redis)
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=current_user.id,
        action=ACTION_DELETE_USER,
        target_type="user",
        target_id=str(user_id),
        detail={"email": user.email},
        ip=audit_service.client_ip(request),
    )
    return result.ok(message="删除成功")


@router.get(
    "/{user_id}/roles",
    summary="获取用户所属角色",
    response_model=Result[list[RoleOut]],
)
async def get_user_roles(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permissions("user:read")),
) -> Result[list[RoleOut]]:
    await _load_user_or_404(db, user_id)
    roles = await rbac_repo.get_user_roles(db, user_id, with_perms=True)
    return result.ok(data=[RoleOut.model_validate(r) for r in roles], message="请求成功")


@router.post(
    "/{user_id}/roles",
    summary="设置用户角色（整组替换）",
    response_model=Result[list[RoleOut]],
)
async def set_user_roles_endpoint(
    user_id: int,
    data: SetUserRolesIn,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permissions("user:write")),
) -> Result[list[RoleOut]]:
    user = await _load_user_or_404(db, user_id)
    await rbac_service.set_user_roles(db, user, data.role_codes)
    roles = await rbac_repo.get_user_roles(db, user_id, with_perms=True)
    return result.ok(data=[RoleOut.model_validate(r) for r in roles], message="角色已更新")
