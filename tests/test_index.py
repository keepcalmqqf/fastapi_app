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
    """未匹配的 API 前缀路径返回统一 Result 格式的 404 JSON（而非 SPA 回退 index.html）。"""
    resp = client.get("/user/not_exist_endpoint")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404
    assert body["data"] is None
    assert body["message"] == "Not Found"
