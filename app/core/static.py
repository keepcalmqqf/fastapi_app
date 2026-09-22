from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# 已知 API 前缀：这些路径未匹配时不回退 index.html，而是抛 404（JSON 统一格式）
API_PREFIXES = (
    "/api",
    "/auth",
    "/user",
    "/member",
    "/health",
    "/metrics",
    "/docs",
    "/openapi.json",
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
