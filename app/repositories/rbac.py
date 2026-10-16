from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Permission, Role, role_permission_table, user_role_table


async def get_user_role_codes(db: AsyncSession, user_id: int) -> set[str]:
    """用户所属全部角色的 code 集合（软删角色不计）。"""
    rows = await db.execute(
        select(Role.code)
        .join(user_role_table, Role.id == user_role_table.c.role_id)
        .where(user_role_table.c.user_id == user_id, Role.deleted_at.is_(None))
    )
    return {r[0] for r in rows.all()}


async def get_user_permission_codes(db: AsyncSession, user_id: int) -> set[str]:
    """用户全部角色聚合出的权限 code 集合（多角色权限取并集）。

    已软删的角色（Role.deleted_at 非空）与已软删的权限均不计入——
    与 get_user_role_codes 一致，确保角色被删除后其权限即时回收，
    不会出现「界面已隐藏角色、接口却仍放行」的判定不一致。
    """
    stmt = (
        select(Permission.code)
        .join(role_permission_table, Permission.id == role_permission_table.c.permission_id)
        .join(Role, Role.id == role_permission_table.c.role_id)
        .join(user_role_table, Role.id == user_role_table.c.role_id)
        .where(
            user_role_table.c.user_id == user_id,
            Role.deleted_at.is_(None),
            Permission.deleted_at.is_(None),
        )
    )
    rows = await db.execute(stmt)
    return {r[0] for r in rows.all()}


async def get_user_roles(db: AsyncSession, user_id: int, with_perms: bool = False) -> list[Role]:
    """用户所属的全部角色（软删角色不计）。"""
    stmt = (
        select(Role)
        .join(user_role_table, Role.id == user_role_table.c.role_id)
        .where(user_role_table.c.user_id == user_id, Role.deleted_at.is_(None))
    )
    if with_perms:
        stmt = stmt.options(selectinload(Role.permissions))
    result = await db.scalars(stmt)
    return list(result.all())


async def get_role_by_code(db: AsyncSession, code: str) -> Role | None:
    return await db.scalar(select(Role).where(Role.code == code, Role.deleted_at.is_(None)))


async def get_role_by_id(db: AsyncSession, role_id: int, with_perms: bool = False) -> Role | None:
    stmt = select(Role).where(Role.id == role_id, Role.deleted_at.is_(None))
    if with_perms:
        stmt = stmt.options(selectinload(Role.permissions))
    return await db.scalar(stmt)


async def list_roles(db: AsyncSession, with_perms: bool = False) -> list[Role]:
    stmt = select(Role).where(Role.deleted_at.is_(None)).order_by(Role.id)
    if with_perms:
        stmt = stmt.options(selectinload(Role.permissions))
    result = await db.scalars(stmt)
    return list(result.all())


async def list_permissions(db: AsyncSession) -> list[Permission]:
    result = await db.scalars(
        select(Permission).where(Permission.deleted_at.is_(None)).order_by(Permission.id)
    )
    return list(result.all())


async def get_permission_by_code(db: AsyncSession, code: str) -> Permission | None:
    return await db.scalar(
        select(Permission).where(Permission.code == code, Permission.deleted_at.is_(None))
    )


async def get_permissions_by_codes(db: AsyncSession, codes: list[str]) -> list[Permission]:
    if not codes:
        return []
    result = await db.scalars(
        select(Permission).where(Permission.code.in_(codes), Permission.deleted_at.is_(None))
    )
    return list(result.all())


async def create_role(
    db: AsyncSession,
    name: str,
    code: str,
    description: str = "",
    is_system: bool = False,
) -> Role:
    role = Role(name=name, code=code, description=description, is_system=is_system)
    db.add(role)
    await db.flush()
    await db.refresh(role)
    return role


async def create_permission(
    db: AsyncSession,
    name: str,
    code: str,
    resource: str = "",
    action: str = "",
    description: str = "",
) -> Permission:
    perm = Permission(
        name=name,
        code=code,
        resource=resource,
        action=action,
        description=description,
    )
    db.add(perm)
    await db.flush()
    await db.refresh(perm)
    return perm


async def update_role(db: AsyncSession, role: Role, fields: dict) -> Role:
    for key, value in fields.items():
        if value is not None:
            setattr(role, key, value)
    await db.flush()
    await db.refresh(role)
    return role


async def set_role_permissions(db: AsyncSession, role: Role, permissions: list[Permission]) -> None:
    """整组替换某角色的权限（先清后插，调用方负责事务提交）。"""
    await db.execute(
        delete(role_permission_table).where(role_permission_table.c.role_id == role.id)
    )
    for perm in permissions:
        await db.execute(
            role_permission_table.insert().values(role_id=role.id, permission_id=perm.id)
        )
    await db.flush()


async def set_user_roles(db: AsyncSession, user_id: int, roles: list[Role]) -> None:
    """整组替换某用户的角色（先清后插，调用方负责事务提交）。"""
    await db.execute(delete(user_role_table).where(user_role_table.c.user_id == user_id))
    for role in roles:
        await db.execute(user_role_table.insert().values(user_id=user_id, role_id=role.id))
    await db.flush()
