import logging
from collections.abc import MutableMapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.routing import Match, Mount

from app.core import result
from app.core.settings import settings

logger = logging.getLogger("fastapi_app")

_RATE_LIMITED_BODY = result.failure(code=429, message="请求过于频繁，请稍后重试").model_dump()


def _matches_api_route(app: FastAPI, scope: MutableMapping[str, Any]) -> bool:
    """路径是否命中已注册的 API 路由；SPA / 静态资源（Mount）不计入限流。

    静态产物（/assets/*.js 等）单次页面加载就有十几个请求，若一并限流，
    前端会因为加载 chunk 被限而弹出「请求过于频繁」，因此只对 API 路径计限。
    """
    for route in app.routes:
        if isinstance(route, Mount):
            continue
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return True
    return False


class _DefaultLimitMiddleware(BaseHTTPMiddleware):
    """对 API 路由执行全局默认限流（`RATE_LIMIT`）。

    **不使用 slowapi 自带的 SlowAPIMiddleware。** 它依赖
    `slowapi.middleware._find_route_handler` 从 `app.routes` 里取 `route.endpoint`，
    而 FastAPI 0.141 起 `include_router` 注册的是没有 `endpoint` 属性的
    `_IncludedRouter`，该查找恒为 None，`_should_exempt` 于是把**每个**请求都判为
    豁免——限流在真实应用上整体静默失效（实测：开启后连续请求依然全部 200）。
    自带中间件还有一个问题：它无法 await 协程异常处理器，会静默回退到 slowapi
    默认处理器，返回 {"error": "Rate limit exceeded: 2 per 1 minute"}，既不是统一
    Result 格式，又把限流阈值泄露给客户端。

    这里改为自行判定「是否 API 路径」，并直接调用 `limiter._check_request_limit`。
    Limiter 的 key_style 为 url，endpoint 传 None 时仍以请求路径为限流键，
    默认限额照常生效。改动前请先跑 tests/test_rate_limit.py。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        limiter: Limiter = request.app.state.limiter
        if not limiter.enabled or not _matches_api_route(request.app, request.scope):
            return await call_next(request)
        try:
            limiter._check_request_limit(request, None, True)
        except RateLimitExceeded:
            return JSONResponse(status_code=429, content=_RATE_LIMITED_BODY)
        except Exception:
            # 限流后端异常时放行（fail-open）并记日志：限流是旁路保护，
            # 不应因存储问题让整个 API 不可用。
            logger.warning("限流校验失败，本次请求放行（fail-open）", exc_info=True)
            return await call_next(request)
        response = await call_next(request)
        # 限流头（X-RateLimit-*）：limit_for_header 由 __evaluate_limits 写入 state，
        # 未参与限流的请求取不到，_inject_headers 对 None 本身是安全的。
        current_limit: Any = getattr(request.state, "view_rate_limit", None)
        return limiter._inject_headers(response, current_limit)


def setup(app: FastAPI) -> None:
    limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT])
    app.state.limiter = limiter
    app.add_middleware(_DefaultLimitMiddleware)

    # 处理器保持同步（不要改成 async def）：供 @limiter.limit() 装饰器路径使用，
    # 而 slowapi 在同步上下文里无法 await 协程处理器。中间件路径已在上面直接返回 429。
    @app.exception_handler(RateLimitExceeded)
    def rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(status_code=429, content=_RATE_LIMITED_BODY)
