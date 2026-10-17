import logging
from collections.abc import Awaitable, Callable, MutableMapping
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

# 本插件绕开 slowapi 自带中间件，直接调用这两个未公开入口（原因见 _DefaultLimitMiddleware
# 的 docstring）。它们在慢速迭代的上游库里随时可能被改名或移除；一旦缺失，限流会退化成
# 「每个请求都命中 except 分支后放行」——与自带中间件曾踩过的静默失效属同一类故障，
# 且更难察觉。因此启动时显式探测，缺失就关闭限流并打 error 日志。
_REQUIRED_INTERNALS = ("_check_request_limit", "_inject_headers")

# 降级标记：挂在 Limiter 实例上，表示本进程已因共享存储不可用退回内存计数
_MEMORY_FALLBACK_MARK = "_rate_limit_memory_fallback"


def _missing_internals(limiter: Any) -> list[str]:
    """返回 Limiter 上缺失的私有入口名称；为空表示兼容。"""
    return [name for name in _REQUIRED_INTERNALS if not hasattr(limiter, name)]


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

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
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
            _downgrade_to_memory(request.app, limiter)
            return await call_next(request)
        response = await call_next(request)
        # 限流头（X-RateLimit-*）：limit_for_header 由 __evaluate_limits 写入 state，
        # 未参与限流的请求取不到，_inject_headers 对 None 本身是安全的。
        current_limit: Any = getattr(request.state, "view_rate_limit", None)
        try:
            return limiter._inject_headers(response, current_limit)
        except Exception:
            # 响应头是增强信息，注入失败不该让已经完成的请求变成 500
            logger.warning("限流响应头注入失败，忽略", exc_info=True)
            return response


def _build_limiter(storage_uri: str, *, enabled: bool = True) -> Limiter:
    return Limiter(
        key_func=get_remote_address,
        default_limits=[settings.RATE_LIMIT],
        # 响应头（X-RateLimit-Limit/Remaining/Reset）便于客户端自行退避
        headers_enabled=True,
        storage_uri=storage_uri,
        enabled=enabled,
    )


def _downgrade_to_memory(app: FastAPI, limiter: Limiter) -> None:
    """共享存储不可用（Redis 宕机等）时把本进程的计数退回内存，只执行一次。

    不降级的话，存储故障期间每个请求都会走 fail-open，限流等于整体消失；退回
    内存至少保留一层保护（代价是阈值放大为 RATE_LIMIT × worker 数）。放行的那
    一次请求无法补记，属于可接受的误差。

    不做自动恢复：存储恢复后需重启进程回到共享计数，避免反复探测带来的抖动。
    """
    if getattr(limiter, _MEMORY_FALLBACK_MARK, False):
        return
    try:
        fallback = _build_limiter(settings.RATE_LIMIT_MEMORY_STORAGE_URI, enabled=limiter.enabled)
        setattr(fallback, _MEMORY_FALLBACK_MARK, True)
    except Exception:
        logger.warning("限流降级到内存计数失败，保持 fail-open", exc_info=True)
        return
    app.state.limiter = fallback
    logger.error(
        "限流共享存储不可用，本进程已降级为内存计数：多 worker 下实际阈值放大为 RATE_LIMIT × "
        "worker 数；存储恢复后需重启进程生效"
    )


def setup(app: FastAPI) -> None:
    storage_uri = settings.rate_limit_storage_uri
    limiter = _build_limiter(storage_uri)
    missing = _missing_internals(limiter)
    if missing:
        # 依赖的私有入口不存在：显式关闭限流并告警，而不是让它悄悄变成空操作
        limiter.enabled = False
        logger.error(
            "slowapi 版本不兼容（缺少 %s），限流已关闭；请检查 slowapi 版本或改回自带中间件",
            ", ".join(missing),
        )
    app.state.limiter = limiter
    if limiter.enabled:
        logger.info("插件已启用: rate_limit (默认 %s)", settings.RATE_LIMIT)
        logger.info(
            "限流存储: %s",
            "共享计数（Redis）"
            if storage_uri != settings.RATE_LIMIT_MEMORY_STORAGE_URI
            else "进程内存（多 worker 下阈值会放大）",
        )
    app.add_middleware(_DefaultLimitMiddleware)

    # 处理器保持同步（不要改成 async def）：供 @limiter.limit() 装饰器路径使用，
    # 而 slowapi 在同步上下文里无法 await 协程处理器。中间件路径已在上面直接返回 429。
    @app.exception_handler(RateLimitExceeded)
    def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(status_code=429, content=_RATE_LIMITED_BODY)
