from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import result
from app.core.database import get_db
from app.core.rbac import require_permissions
from app.core.result import Result
from app.models import Role
from app.repositories import rbac as rbac_repo
from app.schemas.rbac import (
    CreateRole,
    RoleOut,
    SetRolePermissionsIn,
    UpdateRole,
)
from app.services import audit as audit_service
from app.services import rbac as rbac_service
from app.services.audit import ACTION_CREATE_USER, ACTION_DELETE_USER, ACTION_UPDATE_USER

router = APIRouter(prefix="/role", tags=["角色权限"])

# 注意注册顺序：/role/{role_id}/permissions 是更长的固定后缀路径，
# 不会与 /role/{role_id} 单段路径参数冲突，无需刻意调整。


async def _load_role_or_404(db: AsyncSession, role_id: int) -> Role:
    role = await rbac_repo.get_role_by_id(db, role_id, with_perms=True)
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return role


@router.get("/", summary="角色列表（含权限）", response_model=Result[list[RoleOut]])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permissions("role:read")),
) -> Result[list[RoleOut]]:
    roles = await rbac_repo.list_roles(db, with_perms=True)
    return result.ok(data=[RoleOut.model_validate(r) for r in roles], message="请求成功")


@router.post("/", summary="创建角色", response_model=Result[RoleOut])
async def create_role(
    data: CreateRole,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(require_permissions("role:write")),
) -> Result[RoleOut]:
    created = await rbac_service.create_role(db, data.name, data.code, data.description)
    if created is None:
        raise HTTPException(status_code=409, detail="角色 code 已存在")
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=current_user.id,
        action=ACTION_CREATE_USER,
        target_type="role",
        target_id=str(created.id),
        detail={"code": created.code, "name": created.name},
        ip=audit_service.client_ip(request),
    )
    role = await rbac_repo.get_role_by_id(db, created.id, with_perms=True)
    return result.ok(data=RoleOut.model_validate(role), message="创建成功")


@router.get("/{role_id}", summary="角色详情（含权限）", response_model=Result[RoleOut])
async def get_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permissions("role:read")),
) -> Result[RoleOut]:
    role = await _load_role_or_404(db, role_id)
    return result.ok(data=RoleOut.model_validate(role), message="请求成功")


@router.patch("/{role_id}", summary="更新角色", response_model=Result[RoleOut])
async def update_role(
    role_id: int,
    data: UpdateRole,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(require_permissions("role:write")),
) -> Result[RoleOut]:
    role = await _load_role_or_404(db, role_id)
    fields = data.model_dump(exclude_unset=True)
    await rbac_service.update_role(db, role, fields)
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=current_user.id,
        action=ACTION_UPDATE_USER,
        target_type="role",
        target_id=str(role_id),
        detail=fields,
        ip=audit_service.client_ip(request),
    )
    refreshed = await rbac_repo.get_role_by_id(db, role_id, with_perms=True)
    return result.ok(data=RoleOut.model_validate(refreshed), message="更新成功")


@router.delete("/{role_id}", summary="删除角色（软删除）", response_model=Result[None])
async def delete_role(
    role_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(require_permissions("role:write")),
) -> Result[None]:
    role = await _load_role_or_404(db, role_id)
    if role.is_system:
        raise HTTPException(status_code=400, detail="系统内置角色不可删除")
    await rbac_service.delete_role(db, role)
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=current_user.id,
        action=ACTION_DELETE_USER,
        target_type="role",
        target_id=str(role_id),
        detail={"code": role.code},
        ip=audit_service.client_ip(request),
    )
    return result.ok(message="删除成功")


@router.post(
    "/{role_id}/permissions",
    summary="设置角色权限（整组替换）",
    response_model=Result[RoleOut],
)
async def set_role_permissions(
    role_id: int,
    data: SetRolePermissionsIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(require_permissions("role:write")),
) -> Result[RoleOut]:
    role = await _load_role_or_404(db, role_id)
    await rbac_service.set_role_permissions(db, role, data.permission_codes)
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=current_user.id,
        action=ACTION_UPDATE_USER,
        target_type="role",
        target_id=str(role_id),
        detail={"permission_codes": data.permission_codes},
        ip=audit_service.client_ip(request),
    )
    # expire_on_commit=False 下提交不会自动失效关系，需手动刷新 permissions
    # 关联集合，否则 RoleOut 序列化出的是提交前的旧（空）权限
    refreshed = await rbac_repo.get_role_by_id(db, role_id, with_perms=True)
    await db.refresh(refreshed, attribute_names=["permissions"])
    return result.ok(data=RoleOut.model_validate(refreshed), message="权限已更新")
