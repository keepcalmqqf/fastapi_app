from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.core.settings import settings

# 显式列出允许的方法而非通配：与 CORS_ORIGINS 禁通配保持同样的最小权限风格。
# PATCH 必须列出——后台的「更新用户/会员/角色」都走 PATCH，缺失时浏览器预检会被
# Starlette 判为不允许的方法并返回 400（同源部署或 dev 代理下不触发，只在前后端
# 分域部署时暴露）。HEAD 一并提供，便于探活与资源存在性检查。
_ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# 静态资源缓存与安全相关的响应头。
# 哈希产物可长期缓存；index.html 必须禁用缓存，否则发版后浏览器复用旧页面去请求
# 已删除的 hashed chunk，而 SPA 回退会把缺失资源也返回 index.html（200 + HTML），
# 前端解析成 JS 报错，表现为整站白屏。
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def cors_middleware(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=_ALLOWED_METHODS,
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Requested-With"],
    )


def response_headers_middleware(app: FastAPI) -> None:
    """统一补响应头：静态资源缓存策略 + 基础安全头。

    CSP 不在本批次内：Element Plus 大量依赖 JS 写行内 style，直接上 CSP 会把后台
    布局打散，需单独以 Report-Only 观察一轮后再收紧。
    HSTS 仅在 PROD 下发：本地 http 调试下发会让浏览器后续强制走 https。
    """

    @app.middleware("http")
    async def _add_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif path == "/" or path.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache"
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if settings.MODE == "PROD":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
