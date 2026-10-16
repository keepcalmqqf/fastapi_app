"""一次性脚本：在真实库上播种 RBAC 基础数据，并给尚无角色的用户补授超级管理员。

只在「已有用户、但 RBAC 迁移后才首次引入角色体系」的存量库上使用，例如本地开发库。
幂等：角色/权限已存在则跳过；已拥有角色的用户不会被重复改动。

用法（在项目根目录）：
    uv run python scripts/seed_rbac.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# 允许以脚本形式直接运行（项目根目录加入导入路径）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.redis import create_redis
from app.models import User
from app.plugins.cache import set_redis
from app.services import rbac as rbac_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_rbac")


async def main() -> None:
    # 初始化 Redis 客户端（与 app 的 lifespan 一致），使角色/权限变更后的缓存失效逻辑可用；
    # 若 Redis 未运行，缓存失效会优雅降级（仅记录告警），不影响数据写入。
    set_redis(create_redis())

    async with AsyncSessionLocal() as db:
        # 1) 幂等播种：超管 / 管理员 / 访客三角色 + 7 个权限点
        await rbac_service.ensure_seeded(db)
        logger.info("RBAC 角色/权限种子数据已就绪（已存在则跳过）")

        # 2) 给尚无任何角色的用户补授超级管理员
        users = (await db.execute(select(User).where(User.deleted_at.is_(None)))).scalars().all()

        granted: list[str] = []
        for u in users:
            codes = await rbac_service.get_user_role_codes(db, u.id)
            if not codes:
                ok = await rbac_service.assign_role_to_user(db, u, rbac_service.SUPER_ADMIN_CODE)
                if ok:
                    granted.append(f"id={u.id} email={u.email}")
                    logger.info("已授予 super_admin：id=%s email=%s", u.id, u.email)

        if not granted:
            logger.info("没有需要补授角色的用户（均已拥有角色或库为空）")
        else:
            logger.info("本次补授 super_admin 的用户：%s", ", ".join(granted))


if __name__ == "__main__":
    asyncio.run(main())
