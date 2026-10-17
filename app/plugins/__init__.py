import logging

from fastapi import FastAPI

from app.core.settings import settings

logger = logging.getLogger("fastapi_app")


def setup_plugins(app: FastAPI) -> None:
    """按配置开关挂载可插拔能力。新增插件：建模块、写 setup(app)、在此登记。"""
    if settings.ENABLE_REQUEST_ID:
        from app.plugins import request_id

        request_id.setup(app)
        logger.info("插件已启用: request_id")
    if settings.ENABLE_RATE_LIMIT:
        from app.plugins import rate_limit

        rate_limit.setup(app)
        logger.info("插件已启用: rate_limit (%s)", settings.RATE_LIMIT)
    if settings.ENABLE_METRICS:
        from app.plugins import metrics

        metrics.setup(app)
        logger.info("插件已启用: metrics (/metrics)")
    if settings.ENABLE_I18N:
        from app.plugins import i18n

        i18n.setup(app)
        logger.info("插件已启用: i18n (/api/v1/i18n)")
    if settings.ENABLE_WEBSOCKET:
        from app.plugins import websocket

        websocket.setup(app)
        logger.info("插件已启用: websocket (%s)", settings.WEBSOCKET_PATH)
    if settings.ENABLE_TASKS:
        from app.plugins import tasks

        tasks.setup(app)
        logger.info("插件已启用: tasks (worker=%d)", settings.TASK_WORKERS)
    if settings.ENABLE_SCHEDULER:
        from app.plugins import scheduler

        scheduler.setup(app)
        logger.info("插件已启用: scheduler (timezone=%s)", settings.SCHEDULER_TIMEZONE)
