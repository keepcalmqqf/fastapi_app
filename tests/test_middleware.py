"""响应头中间件测试：静态资源缓存策略与安全头。"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.middleware import response_headers_middleware


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


def test_cors_allows_patch():
    """PATCH 必须在允许方法内：后台更新用户/会员/角色都走 PATCH。

    缺失时前后端分域部署的浏览器预检会被 Starlette 判为不允许的方法并返回 400。
    """
    from app.core.middleware import _ALLOWED_METHODS

    assert "PATCH" in _ALLOWED_METHODS
    assert "HEAD" in _ALLOWED_METHODS
