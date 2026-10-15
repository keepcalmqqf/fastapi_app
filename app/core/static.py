from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# 已知 API 前缀：这些路径未匹配时不回退 index.html，而是抛 404（JSON 统一格式）
API_PREFIXES = (
    "/api",  # 业务接口统一前缀 /api/v1/...（见 app/api/__init__.py）
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    # 历史业务前缀（/auth、/user、/member、/audit 已迁至 /api/v1 下）：保留条目
    # 只为让未迁移的调用方拿到 JSON 404，而不是被回退成前端页面（GET 会返回
    # HTML 200、POST 会返回 405，都比 404 更难排查）
    "/auth",
    "/user",
    "/member",
    "/audit",
)


class SPAStaticFiles(StaticFiles):
    """SPA 静态资源托管：未命中的路径回退到 index.html，交由前端路由处理。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                request_path = scope.get("path", "")
                is_api_path = any(
                    request_path == p
                    or request_path.startswith(p + "/")
                    or request_path.startswith(p + "?")
                    for p in API_PREFIXES
                )
                if is_api_path:
                    raise
                return await super().get_response("index.html", scope)
            raise
