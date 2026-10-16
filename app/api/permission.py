from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import result
from app.core.database import get_db
from app.core.rbac import require_permissions
from app.core.result import Result
from app.repositories import rbac as rbac_repo
from app.schemas.rbac import CreatePermission, PermissionOut
from app.services import audit as audit_service
from app.services import rbac as rbac_service
from app.services.audit import ACTION_CREATE_USER

router = APIRouter(prefix="/permission", tags=["权限点"])

# 权限点一般随角色一起配置，单独增删场景较少；此处提供查询与创建接口，
# 删除/修改交由角色权限分配完成，避免权限点游离于角色体系之外。


@router.get("/", summary="权限点列表", response_model=Result[list[PermissionOut]])
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permissions("permission:read")),
) -> Result[list[PermissionOut]]:
    perms = await rbac_repo.list_permissions(db)
    return result.ok(data=[PermissionOut.model_validate(p) for p in perms], message="请求成功")


@router.post("/", summary="创建权限点", response_model=Result[PermissionOut])
async def create_permission(
    data: CreatePermission,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(require_permissions("role:write")),
) -> Result[PermissionOut]:
    created = await rbac_service.create_permission(
        db,
        data.name,
        data.code,
        data.resource,
        data.action,
        data.description,
    )
    if created is None:
        raise HTTPException(status_code=409, detail="权限 code 已存在")
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=current_user.id,
        action=ACTION_CREATE_USER,
        target_type="permission",
        target_id=str(created.id),
        detail={"code": created.code, "name": created.name},
        ip=audit_service.client_ip(request),
    )
    return result.ok(data=PermissionOut.model_validate(created), message="创建成功")
