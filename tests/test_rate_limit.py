"""rate_limit 插件测试：超限返回统一格式 429，静态资源豁免，路由注册方式无关。

插件默认关闭（`ENABLE_RATE_LIMIT=false`），此前完全无测试覆盖。
这里的用例直接在独立 app 上施加极低限额，覆盖两条历史缺陷：
1. slowapi 自带中间件在 FastAPI 0.141（include_router 产生无 endpoint 的
   _IncludedRouter）下把每个请求都判为豁免，限流整体失效；
2. 它还会绕过统一响应处理器，返回 slowapi 默认 body 并泄露限流阈值。
"""

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address
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


def test_header_injection_failure_does_not_break_response(monkeypatch):
    """注入限流响应头失败时，已完成的请求仍正常返回（响应头只是增强信息）。"""
    monkeypatch.setattr(settings, "RATE_LIMIT", "100/minute")
    app = _decorated_app()

    def boom(*args, **kwargs):
        raise RuntimeError("注入失败（测试模拟）")

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.limiter, "_inject_headers", boom)
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_rate_limit_headers_exposed(monkeypatch):
    """响应带 X-RateLimit-* 头，客户端可据此自行退避（headers_enabled 曾为默认 False）。"""
    monkeypatch.setattr(settings, "RATE_LIMIT", "5/minute")
    with TestClient(_decorated_app()) as client:
        resp = client.get("/ping")

    assert resp.status_code == 200
    assert resp.headers.get("x-ratelimit-limit") == "5"


def _capture_limiter_kwargs(monkeypatch) -> dict[str, object]:
    """拦截 Limiter.__init__，返回它实际收到的关键字参数。"""
    captured: dict[str, object] = {}
    real_init = Limiter.__init__

    def spy(self, *args, **kwargs):
        captured.update(kwargs)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(Limiter, "__init__", spy)
    return captured


def test_storage_uri_defaults_to_shared_redis(monkeypatch):
    """未显式配置时自动推导 Redis 连接串，让多 worker 共用一份计数。

    进程内存模式下各 worker 独立累加，实际阈值放大成 RATE_LIMIT × worker 数，
    等于配了个假限额（实测 2 worker + 100/minute ≈ 200/minute）。
    """
    captured = _capture_limiter_kwargs(monkeypatch)
    monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", None)
    monkeypatch.setattr(settings, "REDIS_HOST", "redis")
    monkeypatch.setattr(settings, "REDIS_PORT", 6379)
    monkeypatch.setattr(settings, "REDIS_PASSWORD", "p@ss/w:rd")

    _decorated_app()
    # 凭据做 URL 编码：不编码时含 @ / : 的密码会拼出非法连接串
    assert captured["storage_uri"] == "redis://:p%40ss%2Fw%3Ard@redis:6379/0"
    assert captured["headers_enabled"] is True


def test_explicit_memory_uri_is_respected(monkeypatch):
    """显式指定 memory:// 时不推导 Redis——单机调试与测试的逃生舱。"""
    captured = _capture_limiter_kwargs(monkeypatch)
    monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", settings.RATE_LIMIT_MEMORY_STORAGE_URI)

    _decorated_app()
    assert captured["storage_uri"] == "memory://"


def test_shared_storage_error_downgrades_to_memory(monkeypatch):
    """共享存储不可用（Redis 宕机）时降级为内存计数，而不是持续 fail-open。

    不降级的话故障期间每个请求都放行，限流整体消失；降级后至少保留一层保护，
    代价是阈值放大为 RATE_LIMIT × worker 数。
    """
    monkeypatch.setattr(settings, "RATE_LIMIT", "2/minute")
    monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", "redis://127.0.0.1:1/0")
    app = _decorated_app()

    def boom(*args, **kwargs):
        raise RuntimeError("Redis 不可用（测试模拟）")

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.limiter, "_check_request_limit", boom)
        assert client.get("/ping").status_code == 200  # 首次故障放行并触发降级
        # 降级后内存计数继续生效：后续请求仍会撞上限额
        assert client.get("/ping").status_code == 200
        assert client.get("/ping").status_code == 200
        assert client.get("/ping").status_code == 429

    assert getattr(app.state.limiter, rate_limit_plugin._MEMORY_FALLBACK_MARK) is True


def test_incompatible_limiter_disables_rate_limiting():
    """依赖的 slowapi 私有入口缺失时，限流显式关闭而不是静默失效。"""

    class _Stub:
        pass

    missing = rate_limit_plugin._missing_internals(_Stub())
    assert missing == list(rate_limit_plugin._REQUIRED_INTERNALS)

    # 当前 slowapi 版本应完全兼容
    assert rate_limit_plugin._missing_internals(Limiter(key_func=get_remote_address)) == []
