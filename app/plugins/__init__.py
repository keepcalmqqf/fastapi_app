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
