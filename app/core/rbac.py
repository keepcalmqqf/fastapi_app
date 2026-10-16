import json

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import User
from app.services import rbac as rbac_service


async def assert_permissions(
    db: AsyncSession, user_id: int, *codes: str, require_all: bool = True
) -> None:
    """校验用户是否拥有指定权限；无权则抛 403。超级管理员放行一切。

    供依赖工厂与需要在逻辑分支中手动鉴权的场景（如自举创建用户）共用。
    """
    role_codes = await rbac_service.get_user_role_codes(db, user_id)
    if rbac_service.SUPER_ADMIN_CODE in role_codes:
        return
    perm_codes = await rbac_service.get_user_permission_codes(db, user_id)
    if require_all:
        missing = [c for c in codes if c not in perm_codes]
        if missing:
            # 文案模板与参数分离，交由异常处理层翻译后插值（直接拼好会失去翻译能力）
            detail = "权限不足，缺少：{missing}|" + json.dumps({"missing": ", ".join(missing)})
            raise HTTPException(status_code=403, detail=detail)
    else:
        if not any(c in perm_codes for c in codes):
            raise HTTPException(status_code=403, detail="权限不足")


async def assert_roles(db: AsyncSession, user_id: int, *codes: str) -> None:
    """校验用户是否拥有任一指定角色；无权则抛 403。超级管理员放行一切。"""
    role_codes = await rbac_service.get_user_role_codes(db, user_id)
    if rbac_service.SUPER_ADMIN_CODE in role_codes:
        return
    if not any(c in role_codes for c in codes):
        raise HTTPException(status_code=403, detail="角色无权访问该资源")


def require_permissions(*codes: str, require_all: bool = True):
    """权限守卫依赖工厂。

    挂在接口上即可按权限 code 拦截：当前用户缺权限时返回 403。
    返回 current_user，便于端点直接复用。

    用法:
        current_user: User = Depends(require_permissions("user:write"))
        # 或仅作守卫（丢弃返回值）
        Depends(require_permissions("user:read"))
    """

    async def dependency(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        await assert_permissions(db, current_user.id, *codes, require_all=require_all)
        return current_user

    return dependency


def require_roles(*codes: str):
    """角色守卫依赖工厂：当前用户拥有任一指定角色 code 才放行，返回 current_user。"""

    async def dependency(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        await assert_roles(db, current_user.id, *codes)
        return current_user

    return dependency
