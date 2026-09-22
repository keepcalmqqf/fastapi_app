"""metrics 插件测试：配置 METRICS_TOKEN 后 /metrics 需要 Bearer 鉴权，未配置保持公开。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.plugins import metrics as metrics_plugin


def _build_metrics_app() -> FastAPI:
    app = FastAPI()
    metrics_plugin.setup(app)
    return app


def test_metrics_public_without_token(monkeypatch):
    """未配置 METRICS_TOKEN 时 /metrics 公开访问。"""
    monkeypatch.setattr(settings, "METRICS_TOKEN", None)
    with TestClient(_build_metrics_app()) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_requires_bearer_token(monkeypatch):
    """配置 METRICS_TOKEN 后：无 token 或 token 错误返回 401，正确 Bearer 放行。"""
    monkeypatch.setattr(settings, "METRICS_TOKEN", "metrics-secret")
    with TestClient(_build_metrics_app()) as client:
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        resp = client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})
    assert resp.status_code == 200
