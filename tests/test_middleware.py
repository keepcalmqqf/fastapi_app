"""响应头中间件测试：静态资源缓存策略与安全头。"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.middleware import cors_middleware, response_headers_middleware


def _app() -> TestClient:
    app = FastAPI()
    response_headers_middleware(app)

    @app.get("/")
    async def index() -> dict:
        return {"ok": True}

    @app.get("/assets/app.1234.js")
    async def asset() -> dict:
        return {"ok": True}

    @app.get("/some/page.html")
    async def page() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_index_html_disables_cache():
    """SPA 入口页必须 no-cache。

    旧页面被复用后会去请求已删除的 hashed chunk，而 SPA 回退会把缺失资源也返回
    index.html（200 + HTML），前端解析成 JS 报错，表现为整站白屏。
    """
    client = _app()
    assert client.get("/").headers["Cache-Control"] == "no-cache"
    assert client.get("/some/page.html").headers["Cache-Control"] == "no-cache"


def test_hashed_assets_are_immutable():
    """带哈希的产物可长期缓存。"""
    client = _app()
    cache = client.get("/assets/app.1234.js").headers["Cache-Control"]
    assert "max-age=31536000" in cache
    assert "immutable" in cache


def test_security_headers_on_every_response():
    """安全头随所有响应下发（CSP 不在本批次，需单独以 Report-Only 观察）。"""
    client = _app()
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def _cors_app() -> TestClient:
    """挂上真实 CORS 中间件的 app：预检响应头只有经中间件才会出现。"""
    app = FastAPI()
    cors_middleware(app)

    @app.patch("/api/v1/user/{user_id}")
    async def patch_user(user_id: int) -> dict:
        return {"ok": True}

    return TestClient(app)


def test_cors_allows_patch():
    """PATCH 必须在允许方法内：后台更新用户/会员/角色都走 PATCH。

    缺失时前后端分域部署的浏览器预检会被 Starlette 判为不允许的方法并返回 400。

    此前这条用例直接断言模块级常量 `_ALLOWED_METHODS`——即使 `cors_middleware`
    忘了把这个列表传给 CORSMiddleware，它照样是绿的。断言必须落到「预检请求的
    真实响应头」上：那才是浏览器实际读取的东西。
    """
    client = _cors_app()
    resp = client.options(
        "/api/v1/user/",
        headers={
            "Origin": "http://localhost:8080",
            "Access-Control-Request-Method": "PATCH",
        },
    )
    allowed = resp.headers.get("access-control-allow-methods", "")
    assert "PATCH" in allowed, allowed
    assert "DELETE" in allowed, allowed


def test_cors_preflight_rejects_unknown_origin():
    """白名单外的源不回 CORS 头（allow_credentials 已启用，不能反射任意 Origin）。"""
    client = _cors_app()
    resp = client.options(
        "/api/v1/user/",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "PATCH",
        },
    )
    assert "access-control-allow-origin" not in resp.headers
