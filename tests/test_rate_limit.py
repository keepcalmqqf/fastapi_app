"""rate_limit 插件测试：超限返回统一格式 429，静态资源豁免，路由注册方式无关。

插件默认关闭（`ENABLE_RATE_LIMIT=false`），此前完全无测试覆盖。
这里的用例直接在独立 app 上施加极低限额，覆盖两条历史缺陷：
1. slowapi 自带中间件在 FastAPI 0.141（include_router 产生无 endpoint 的
   _IncludedRouter）下把每个请求都判为豁免，限流整体失效；
2. 它还会绕过统一响应处理器，返回 slowapi 默认 body 并泄露限流阈值。
"""

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.staticfiles import StaticFiles

from app.core.settings import settings
from app.plugins import rate_limit as rate_limit_plugin


def _decorated_app() -> FastAPI:
    """路由直接挂在 app 上（APIRoute 带 endpoint）。"""
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    rate_limit_plugin.setup(app)
    return app


def _included_router_app() -> FastAPI:
    """路由经 include_router 注册——复现 FastAPI 0.141 的 _IncludedRouter 结构。"""
    app = FastAPI()
    router = APIRouter()

    @router.get("/ping")
    async def ping():
        return {"ok": True}

    app.include_router(router)
    rate_limit_plugin.setup(app)
    return app


def test_requests_within_limit_pass(monkeypatch):
    """限额内请求正常返回，不受限流中间件影响。"""
    monkeypatch.setattr(settings, "RATE_LIMIT", "5/minute")
    with TestClient(_decorated_app()) as client:
        assert client.get("/ping").status_code == 200
        assert client.get("/ping").status_code == 200


def test_rate_limit_exceeded_returns_unified_429(monkeypatch):
    """超出限额返回 429，且响应体为统一 Result 格式（code/data/message）。"""
    monkeypatch.setattr(settings, "RATE_LIMIT", "2/minute")
    with TestClient(_decorated_app()) as client:
        assert client.get("/ping").status_code == 200
        assert client.get("/ping").status_code == 200
        resp = client.get("/ping")

    assert resp.status_code == 429
    body = resp.json()
    assert set(body) == {"code", "data", "message"}
    assert body["code"] == 429
    assert body["data"] is None
    assert body["message"] == "请求过于频繁，请稍后重试"
    # 不得泄露限流阈值等内部细节
    assert "2 per" not in resp.text


def test_rate_limit_applies_to_included_router(monkeypatch):
    """回归：经 include_router 注册的路由同样受限（旧实现此处恒为 200）。"""
    monkeypatch.setattr(settings, "RATE_LIMIT", "2/minute")
    with TestClient(_included_router_app()) as client:
        assert client.get("/ping").status_code == 200
        assert client.get("/ping").status_code == 200
        resp = client.get("/ping")

    assert resp.status_code == 429
    assert resp.json()["code"] == 429


def test_static_mount_is_not_rate_limited(monkeypatch, tmp_path):
    """SPA / 静态资源不计入限流，避免前端加载 chunk 被误伤成 429。"""
    monkeypatch.setattr(settings, "RATE_LIMIT", "2/minute")
    app = FastAPI()

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    app.mount("/assets", StaticFiles(directory=str(tmp_path)), name="assets")
    rate_limit_plugin.setup(app)

    with TestClient(app) as client:
        for _ in range(5):
            resp = client.get("/assets/app.js")
            assert resp.status_code != 429
        # API 路径仍受限，证明中间件本身在工作
        assert client.get("/api/ping").status_code == 200
        assert client.get("/api/ping").status_code == 200
        assert client.get("/api/ping").status_code == 429


def test_rate_limit_fails_open_on_storage_error(monkeypatch):
    """限流存储异常时放行并记日志：限流是旁路保护，不应让整个 API 不可用。"""
    monkeypatch.setattr(settings, "RATE_LIMIT", "2/minute")
    app = _decorated_app()

    def boom(*args, **kwargs):
        raise RuntimeError("限流存储不可用（测试模拟）")

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.limiter, "_check_request_limit", boom)
        for _ in range(3):
            assert client.get("/ping").status_code == 200
