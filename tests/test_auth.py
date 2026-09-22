"""认证域测试：登录双 Cookie、refresh 轮换、logout 黑名单、Cookie 来源 CSRF 防护。"""

from http.cookies import SimpleCookie
from typing import Any

from app.core.settings import settings

USER_PAYLOAD: dict[str, Any] = {
    "name": "张三",
    "email": "zhangsan@test.com",
    "password": "abcd1234",
    "is_active": True,
}

CSRF_HEADER = {"X-Requested-With": "XMLHttpRequest"}


def _set_cookie(response, name: str):
    """从 Set-Cookie 响应头解析指定 Cookie 的 Morsel（可读 httponly/samesite/secure 等属性）。"""
    for header in response.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(header)
        if name in jar:
            return jar[name]
    return None


def _create_admin(client) -> None:
    resp = client.post("/user/create_user", json=USER_PAYLOAD)
    assert resp.status_code == 200, resp.text


def _login(client, email: str = USER_PAYLOAD["email"], password: str = USER_PAYLOAD["password"]):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- 登录与 Cookie ----------


def test_login_returns_token_out_and_sets_cookies(client):
    """登录返回 TokenOut 结构，并种下 access/refresh 双 HttpOnly Cookie。"""
    _create_admin(client)
    resp = _login(client)
    data = resp.json()["data"]
    assert set(data) >= {"access_token", "refresh_token", "token_type", "expires_in"}
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert data["access_token"] != data["refresh_token"]

    for name in ("access_token", "refresh_token"):
        morsel = _set_cookie(resp, name)
        assert morsel is not None, f"缺少 Cookie: {name}"
        assert morsel["httponly"] is True
        assert morsel["samesite"].lower() == "lax"
        assert bool(morsel["secure"]) is settings.cookie_secure
        assert morsel.value == data[name]


# ---------- refresh 轮换 ----------


def test_refresh_rotates_and_rejects_reuse(client):
    """refresh 轮换成功签发新对；旧 refresh 重用返回 401。"""
    _create_admin(client)
    old_refresh = _login(client).json()["data"]["refresh_token"]

    resp = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200, resp.text
    new_data = resp.json()["data"]
    assert new_data["access_token"] and new_data["refresh_token"]
    assert new_data["refresh_token"] != old_refresh

    # 旧 refresh jti 已删除，重用即 401（先清 Cookie jar，避免有效 Cookie 优先于 body）
    client.cookies.clear()
    resp = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 401


def test_refresh_rejects_access_token(client):
    """access token 冒充 refresh token 返回 401（缺少 type=refresh）。"""
    _create_admin(client)
    access = _login(client).json()["data"]["access_token"]
    client.cookies.clear()
    resp = client.post("/auth/refresh", json={"refresh_token": access})
    assert resp.status_code == 401


def test_refresh_via_cookie_without_body(client):
    """cookie 优先：登录后客户端携带 refresh Cookie 即可刷新，无需 body。"""
    _create_admin(client)
    _login(client)
    resp = client.post("/auth/refresh")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["access_token"]


def test_refresh_without_token(client):
    resp = client.post("/auth/refresh")
    assert resp.status_code == 401


# ---------- logout 与黑名单 ----------


def test_logout_blacklists_access_token(client):
    """logout 后旧 access token（Bearer）访问受保护接口返回 401。"""
    _create_admin(client)
    tokens = _login(client).json()["data"]
    headers = _auth(tokens["access_token"])

    resp = client.post("/auth/logout", headers=headers)
    assert resp.status_code == 200

    me = client.get("/user/me", headers=headers)
    assert me.status_code == 401

    # 旧 refresh 一并撤销
    client.cookies.clear()
    resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def test_logout_clears_cookies(client):
    _create_admin(client)
    _login(client)
    resp = client.post("/auth/logout")
    assert resp.status_code == 200
    for name in ("access_token", "refresh_token"):
        morsel = _set_cookie(resp, name)
        assert morsel is not None and morsel.value == ""


# ---------- Cookie 来源请求的 CSRF 防护 ----------


def test_cookie_write_without_csrf_header_forbidden(client):
    """Cookie 来源的写请求缺少 X-Requested-With 返回 403，携带后通过。"""
    _create_admin(client)
    _login(client)  # TestClient 自动携带后续请求的 Cookie

    payload = {**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"}
    resp = client.post("/user/create_user", json=payload)
    assert resp.status_code == 403

    resp = client.post("/user/create_user", json=payload, headers=CSRF_HEADER)
    assert resp.status_code == 200, resp.text


def test_cookie_read_request_needs_no_csrf_header(client):
    """Cookie 来源的读请求（GET）不需要 CSRF 头。"""
    _create_admin(client)
    _login(client)
    resp = client.get("/user/me")
    assert resp.status_code == 200


def test_bearer_write_needs_no_csrf_header(client):
    """Bearer 来源的写请求不受 CSRF 头约束。"""
    _create_admin(client)
    token = _login(client).json()["data"]["access_token"]
    resp = client.post(
        "/user/create_user",
        json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
