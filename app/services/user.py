from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.models import User
from app.repositories import user as user_repo
from app.schemas.user import CreateUser

# 预置的 dummy 哈希：用户不存在时也执行一次 Argon2 verify，拉平登录耗时，
# 避免通过响应时间差枚举已注册邮箱。
_DUMMY_PASSWORD_HASH = security.hash_password("dummy-password-for-timing-equalization")


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await user_repo.get_user_by_id(db, user_id)


async def needs_bootstrap(db: AsyncSession) -> bool:
    """users 表是否为空（空库时允许无凭证创建首个管理员）。"""
    return await user_repo.count_users(db) == 0


async def create_user(db: AsyncSession, data: CreateUser) -> User | None:
    """创建用户；邮箱已存在（含并发撞唯一约束）时返回 None。事务边界在本层。

    注意：软删除不释放邮箱——已软删账号的邮箱在 repository 预检查中不可见，
    但唯一约束仍在，最终由 IntegrityError 兜底转 409。
    """
    if await user_repo.get_user_by_email(db, data.email) is not None:
        return None
    try:
        user = await user_repo.create_user(db, data, security.hash_password(data.password))
        await db.commit()
    except IntegrityError:
        # 并发注册/软删重建撞唯一约束：flush 或 commit 阶段都可能抛出，统一回滚降级为「已存在」
        await db.rollback()
        return None
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await user_repo.get_user_by_email(db, email)
    # 用户不存在或无密码时改验 dummy 哈希，保证恒有一次 Argon2 计算
    hashed = user.password if user is not None and user.password else _DUMMY_PASSWORD_HASH
    if not security.verify_password(password, hashed):
        return None
    # 密码错误与账号禁用返回同样的 None，避免泄露账号状态
    if user is None or not user.is_active:
        return None
    return user


async def list_users(db: AsyncSession, page: int, page_size: int) -> tuple[list[User], int]:
    return await user_repo.list_users(db, page, page_size)


async def update_user(db: AsyncSession, user: User, data: dict) -> User:
    """更新用户信息（只落非 None 字段）。事务边界在本层。"""
    updated = await user_repo.update_user(db, user, data)
    await db.commit()
    return updated


async def delete_user(db: AsyncSession, user: User) -> None:
    """软删除用户。事务边界在本层。"""
    await user_repo.soft_delete_user(db, user)
    await db.commit()
