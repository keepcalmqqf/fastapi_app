import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient as StarletteClient

from app.core.static import SPAStaticFiles


@pytest.fixture
def spa_app(tmp_path):
    """只挂 SPA 静态托管的裸应用，用来单独验证回退规则（不依赖真实前端产物）。"""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    app = Starlette()
    app.mount("/", SPAStaticFiles(directory=str(dist), html=True))
    return StarletteClient(app)


def test_spa_unknown_path_falls_back_to_index(spa_app):
    """非 API 前缀的未知路径回退 index.html，交给前端路由。"""
    resp = spa_app.get("/some/spa/route")
    assert resp.status_code == 200
    assert "spa" in resp.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/user/1/x"])
def test_api_prefixes_do_not_fall_back_to_spa(spa_app, path):
    """API 前缀未命中必须 404，不能回退成前端 index.html。

    /redoc 曾漏出白名单：PROD 关闭文档后它会返回 200 + 前端 HTML，
    与 /docs、/openapi.json 的 404 语义不一致。
    """
    assert spa_app.get(path).status_code == 404


def test_api_info(client):
    resp = client.get("/api/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["service"] == "fastapi_app"


def test_health_ok(client):
    """mysql/redis 均正常时 /health 返回 200 且 status=ok。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["status"] == "ok"
    assert data["components"] == {"mysql": "ok", "redis": "ok"}


def test_health_degraded_when_redis_down(client, broken_redis):
    """redis 故障时 /health 返回 503 且 status=degraded。"""
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == 503
    data = body["data"]
    assert data["status"] == "degraded"
    assert data["components"]["redis"] == "down"
    assert data["components"]["mysql"] == "ok"


def test_unmatched_api_path_returns_404_result(client):
    """未匹配的 API 前缀路径返回统一 Result 格式的 404 JSON（而非 SPA 回退 index.html）。

    用多一级子路径：`/user/{user_id}` 存在，单段的 `/user/xxx` 会命中它（鉴权先于参数
    校验，所以是 401 而非 404），测不到 SPA 回退这条分支。
    """
    resp = client.get("/user/1/not_exist_endpoint")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404
    assert body["data"] is None
    assert body["message"] == "Not Found"
