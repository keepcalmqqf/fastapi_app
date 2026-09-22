import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.core import result
from app.core.settings import settings

logger = logging.getLogger("fastapi_app")


class _MetricsAuthMiddleware:
    """/metrics 访问鉴权。

    配置 METRICS_TOKEN 后，请求必须携带 Authorization: Bearer <METRICS_TOKEN>，
    否则返回统一格式的 401；未配置时 /metrics 保持公开（向后兼容），
    生产环境应配置 METRICS_TOKEN 以防指标数据泄露。
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path") == "/metrics":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1")
            if auth != f"Bearer {self.token}":
                response = JSONResponse(
                    status_code=401,
                    content=result.failure(code=401, message="未授权访问").model_dump(),
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def setup(app: FastAPI) -> None:
    if settings.METRICS_TOKEN:
        app.add_middleware(_MetricsAuthMiddleware, token=settings.METRICS_TOKEN)
        logger.info("插件已启用: metrics (/metrics，已启用令牌鉴权)")
    else:
        logger.warning("插件已启用: metrics (/metrics 公开访问，生产环境建议配置 METRICS_TOKEN)")
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
