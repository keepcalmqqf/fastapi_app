"""具名任务定义：在这里用 `@task_manager.task("名称")` 注册所有分布式任务。

该模块会被两处导入：
- Web 进程：`task_manager.start()` 在 redis 后端会 import 本模块，填充注册表供名称校验。
- worker 进程：`python -m app.plugins.tasks_worker` 启动时 import 本模块，按名称解析处理器。

约定：
- 处理器签名即普通 `async def handler(*业务参数)`，本实现直接 `handler(*args, **kwargs)`，
  不传 ctx；需要数据库/Redis 时在函数内自行获取。
- 参数须可 JSON 序列化（投递时经 json.dumps 写入 Redis Stream）。
- 处理器应幂等：分布式为 at-least-once，极端下会重复执行。
- 后台任务同样受分层约束：事务只在 service 层 commit；这里只做编排，复杂逻辑调用 service。
"""

from __future__ import annotations

import asyncio
import logging

from app.plugins.tasks import task_manager

logger = logging.getLogger("fastapi_app")


@task_manager.task("send_welcome_email")
async def send_welcome_email(user_id: int) -> dict:
    """示例：发送欢迎邮件（此处用休眠模拟耗时 IO）。"""
    await asyncio.sleep(0.05)
    logger.info("已发送欢迎邮件: user_id=%s", user_id)
    return {"user_id": user_id, "sent": True}


@task_manager.task("cleanup_expired_tokens")
async def cleanup_expired_tokens() -> dict:
    """示例：清理过期令牌（此处仅打日志，真实实现可调 service/repository）。"""
    logger.info("清理过期令牌（示例任务）")
    return {"cleaned": 0}
