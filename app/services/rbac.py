import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Permission, Role, User
from app.models.base import utc_now
from app.plugins.cache import get_redis
from app.repositories import rbac as rbac_repo

logger = logging.getLogger("fastapi_app")

# 超级管理员角色 code：拥有全部权限，所有守卫对其放行
SUPER_ADMIN_CODE = "super_admin"

# 用户权限缓存前缀：key = f"{PREFIX}:{user_id}"，值 = JSON 化的权限 code 列表
_PERM_CACHE_PREFIX = "rbac:user_perms"
_PERM_CACHE_TTL = 300

# 自举/种子预置的权限点：(code, name, resource, action)
_SEED_PERMISSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("user:read", "查看用户", "user", "read"),
    ("user:write", "管理用户", "user", "write"),
    ("member:read", "查看会员", "member", "read"),
    ("member:write", "管理会员", "member", "write"),
    ("role:read", "查看角色权限", "role", "read"),
    ("role:write", "管理角色权限", "role", "write"),
    ("permission:read", "查看权限", "permission", "read"),
    # 审计日志含操作者、来源 IP 与变更详情，属敏感数据，必须鉴权后才能读取
    ("audit:read", "查看审计日志", "audit", "read"),
)

# 自举/种子预置的角色：(code, name, 该角色拥有的权限 code 列表, is_system)
# 访客只给业务读权限：审计日志含 IP 与变更详情，不应向只读账号开放
_SEED_ROLES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    (SUPER_ADMIN_CODE, "超级管理员", tuple(p[0] for p in _SEED_PERMISSIONS), True),
    (
        "admin",
        "管理员",
        ("user:read", "user:write", "member:read", "member:write", "audit:read"),
        True,
    ),
    ("viewer", "访客", ("user:read", "member:read"), True),
)


async def _invalidate_user_cache(user_id: int) -> None:
    redis = get_redis()
    try:
        await redis.delete(f"{_PERM_CACHE_PREFIX}:{user_id}")
    except Exception:
        logger.warning("失效用户权限缓存失败: user_id=%s", user_id, exc_info=True)


async def invalidate_all_user_caches() -> None:
    """角色/权限定义或用户-角色关系变更后，清空全部用户权限缓存。

    权限集合由「用户→角色→权限」两跳推导，任意一跳变化都可能影响任意用户，
    故采用全量失效（写操作低频，代价可接受）。
    """
    redis = get_redis()
    if redis is None:
        return
    try:
        async for key in redis.scan_iter(f"{_PERM_CACHE_PREFIX}:*"):
            await redis.delete(key)
    except Exception:
        logger.warning("批量失效权限缓存失败", exc_info=True)


async def get_user_role_codes(db: AsyncSession, user_id: int) -> set[str]:
    return await rbac_repo.get_user_role_codes(db, user_id)


async def get_user_permission_codes(db: AsyncSession, user_id: int) -> set[str]:
    """用户权限 code 集合：优先命中 Redis 缓存，未命中则从库推导并回写。"""
    redis = get_redis()
    key = f"{_PERM_CACHE_PREFIX}:{user_id}"
    if redis is not None:
        try:
            hit = await redis.get(key)
            if hit is not None:
                return set(json.loads(hit))
        except Exception:
            logger.warning("读取权限缓存失败，回退到数据库", exc_info=True)
    codes = await rbac_repo.get_user_permission_codes(db, user_id)
    if redis is not None:
        try:
            await redis.set(key, json.dumps(sorted(codes)), ex=_PERM_CACHE_TTL)
        except Exception:
            logger.warning("写入权限缓存失败", exc_info=True)
    return codes


async def ensure_seeded(db: AsyncSession) -> None:
    """幂等播种角色与权限：仅当超级管理员角色不存在时才执行。

    空库自举创建首个管理员前调用，保证 RBAC 基础数据就绪；重复调用安全。
    """
    if await rbac_repo.get_role_by_code(db, SUPER_ADMIN_CODE) is not None:
        return
    perms: dict[str, Permission] = {}
    for code, name, resource, action in _SEED_PERMISSIONS:
        perms[code] = await rbac_repo.create_permission(db, name, code, resource, action)
    for code, name, perm_codes, is_system in _SEED_ROLES:
        role = await rbac_repo.create_role(db, name, code, is_system=is_system)
        await rbac_repo.set_role_permissions(db, role, [perms[c] for c in perm_codes if c in perms])
    await db.commit()


async def ensure_seed_updates(db: AsyncSession) -> bool:
    """为已存在角色的库做增量补齐，返回是否发生了变更。

    `ensure_seeded` 在超级管理员角色已存在时会直接返回，因此新增权限点（如
    audit:read）不会自动进入存量库——只改种子代码对已部署环境无效。本函数按
    种子定义补齐「缺失的权限点」与「内置角色缺失的权限关联」，自定义角色不在
    种子定义内故不受影响；无变更时只做查询，不做写操作。
    """
    changed = False
    perms: dict[str, Permission] = {}
    for code, name, resource, action in _SEED_PERMISSIONS:
        existing = await rbac_repo.get_permission_by_code(db, code)
        if existing is None:
            perms[code] = await rbac_repo.create_permission(db, name, code, resource, action)
            changed = True
        else:
            perms[code] = existing
    for code, _name, perm_codes, _is_system in _SEED_ROLES:
        role = await rbac_repo.get_role_by_code(db, code)
        if role is None:
            continue
        current = await rbac_repo.get_role_permission_codes(db, role.id)
        missing = [c for c in perm_codes if c not in current]
        if not missing:
            continue
        await rbac_repo.add_role_permissions(db, role, [perms[c] for c in missing])
        changed = True
    if changed:
        await db.commit()
        # 权限集合变化会影响任意用户，且缓存 TTL 长达 300s，必须主动失效
        await invalidate_all_user_caches()
    return changed


async def assign_role_to_user(db: AsyncSession, user: User, role_code: str) -> bool:
    """将某角色（整组替换）授予用户；角色不存在返回 False。"""
    role = await rbac_repo.get_role_by_code(db, role_code)
    if role is None:
        return False
    await rbac_repo.set_user_roles(db, user.id, [role])
    await db.commit()
    await _invalidate_user_cache(user.id)
    return True


async def set_user_roles(db: AsyncSession, user: User, role_codes: list[str]) -> None:
    """按给定 code 列表整组替换用户的角色；未知 code 自动忽略。"""
    roles: list[Role] = []
    for code in role_codes:
        role = await rbac_repo.get_role_by_code(db, code)
        if role is not None:
            roles.append(role)
    await rbac_repo.set_user_roles(db, user.id, roles)
    await db.commit()
    await _invalidate_user_cache(user.id)


async def set_role_permissions(db: AsyncSession, role: Role, permission_codes: list[str]) -> None:
    """按给定 code 列表整组替换角色的权限；未知 code 自动忽略。"""
    perms = await rbac_repo.get_permissions_by_codes(db, permission_codes)
    await rbac_repo.set_role_permissions(db, role, perms)
    await db.commit()
    await invalidate_all_user_caches()


async def create_role(db: AsyncSession, name: str, code: str, description: str = "") -> Role | None:
    """创建角色；code 已存在返回 None。事务边界在本层。"""
    if await rbac_repo.get_role_by_code(db, code) is not None:
        return None
    role = await rbac_repo.create_role(db, name, code, description)
    await db.commit()
    return role


async def update_role(db: AsyncSession, role: Role, fields: dict) -> Role:
    updated = await rbac_repo.update_role(db, role, fields)
    await db.commit()
    return updated


async def delete_role(db: AsyncSession, role: Role) -> None:
    """软删除角色（系统内置角色由调用方拦截，不在本处判断）。"""
    role.deleted_at = utc_now()
    await db.flush()
    await db.commit()
    await invalidate_all_user_caches()


async def create_permission(
    db: AsyncSession,
    name: str,
    code: str,
    resource: str = "",
    action: str = "",
    description: str = "",
) -> Permission | None:
    if await rbac_repo.get_permission_by_code(db, code) is not None:
        return None
    perm = await rbac_repo.create_permission(db, name, code, resource, action, description)
    await db.commit()
    return perm
