"""认证域测试：登录双 Cookie、refresh 轮换、logout 黑名单、Cookie 来源 CSRF 防护。"""

import asyncio
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Any
from uuid import uuid4

import jwt

from app.core.revocation import is_subject_revoked, revocation_key, revoke_subject
from app.core.settings import settings
from tests.conftest import fake_redis

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


# ---------- 令牌类型隔离：refresh 令牌不得当作访问令牌 ----------


def test_refresh_token_rejected_as_access_token(client):
    """回归：refresh 令牌（type=refresh）作为 Bearer 访问受保护接口一律 401。

    修复前 refresh 令牌可直接通过鉴权（其 aud/sub 均合法），
    读接口与写接口都能成功，等同于把 7 天有效期的刷新凭证当成访问凭证。
    """
    _create_admin(client)
    refresh_token = _login(client).json()["data"]["refresh_token"]

    assert client.get("/user/me", headers=_auth(refresh_token)).status_code == 401
    assert client.get("/user/list", headers=_auth(refresh_token)).status_code == 401

    # 写接口同样拒绝，且不得产生副作用
    resp = client.post(
        "/user/create_user",
        json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"},
        headers=_auth(refresh_token),
    )
    assert resp.status_code == 401


def test_refresh_token_rejected_as_access_token_via_cookie(client):
    """Cookie 来源的 refresh 令牌同样不能充当 access_token Cookie。"""
    _create_admin(client)
    refresh_token = _login(client).json()["data"]["refresh_token"]

    client.cookies.clear()
    client.cookies.set("access_token", refresh_token)
    resp = client.get("/user/me")
    assert resp.status_code == 401


def test_refresh_token_unusable_as_access_token_after_logout(client):
    """回归：登出后 refresh 令牌既不能刷新，也不能绕过黑名单访问受保护接口。

    这是修复前的核心危害路径——用户登出后，被窃取的 refresh 令牌仍可
    调用后台接口（含特权写操作），有效期长达 REFRESH_TOKEN_EXPIRE_DAYS。
    """
    _create_admin(client)
    tokens = _login(client).json()["data"]
    access_token, refresh_token = tokens["access_token"], tokens["refresh_token"]

    # 完整 Cookie 流程登出：access jti 进黑名单、refresh jti 删除、双 Cookie 清空
    resp = client.post("/auth/logout", headers=CSRF_HEADER)
    assert resp.status_code == 200

    # access 令牌已被拉黑
    assert client.get("/user/me", headers=_auth(access_token)).status_code == 401

    client.cookies.clear()
    # refresh 端点已不可用（jti 已从 Redis 删除）
    assert client.post("/auth/refresh", json={"refresh_token": refresh_token}).status_code == 401
    # 关键回归：拿 refresh 令牌直接当访问令牌用，必须 401
    assert client.get("/user/me", headers=_auth(refresh_token)).status_code == 401
    resp = client.post(
        "/user/create_user",
        json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"},
        headers=_auth(refresh_token),
    )
    assert resp.status_code == 401


def test_legacy_access_token_without_type_still_accepted(client):
    """兼容性：历史 access 令牌无 type 字段时仍可访问（白名单放行 None 与 "access"）。"""
    _create_admin(client)
    now = datetime.now(timezone.utc)
    legacy = jwt.encode(
        {
            "sub": "1",
            "aud": "admin",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid4().hex,
        },
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    resp = client.get("/user/me", headers=_auth(legacy))
    assert resp.status_code == 200, resp.text


def test_unknown_token_type_rejected(client):
    """白名单语义：非 access/refresh 的未知类型同样拒绝（避免未来新增令牌类型被误用）。"""
    _create_admin(client)
    now = datetime.now(timezone.utc)
    weird = jwt.encode(
        {
            "sub": "1",
            "aud": "admin",
            "type": "reset",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid4().hex,
        },
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    resp = client.get("/user/me", headers=_auth(weird))
    assert resp.status_code == 401


# ---------- 主体级撤销（app/core/revocation.py） ----------


class _BrokenRedis:
    """所有操作都抛异常的 Redis 替身，用于验证 fail-open 行为。"""

    async def get(self, key: str):
        raise ConnectionError("Redis 不可用（测试模拟）")

    async def set(self, *args, **kwargs):
        raise ConnectionError("Redis 不可用（测试模拟）")


def test_is_subject_revoked_semantics():
    """撤销判定：无标记不撤销；iat <= 撤销时间点撤销；iat 更大（撤销后签发）放行。"""
    fake_redis._data[revocation_key("admin", "1")] = "1000"

    assert asyncio.run(is_subject_revoked("admin", "1", 999, fake_redis)) is True
    # iat 为秒级，同一秒内无法区分先后，统一按「已撤销」处理（宁可误杀不可漏放）
    assert asyncio.run(is_subject_revoked("admin", "1", 1000, fake_redis)) is True
    # 撤销之后签发的令牌不受影响，保证重新启用后仍能正常登录
    assert asyncio.run(is_subject_revoked("admin", "1", 1001, fake_redis)) is False
    # 其他主体 / 缺少 iat / Redis 未初始化：均不撤销
    assert asyncio.run(is_subject_revoked("admin", "2", 1, fake_redis)) is False
    assert asyncio.run(is_subject_revoked("member", "1", 1, fake_redis)) is False
    assert asyncio.run(is_subject_revoked("admin", "1", None, fake_redis)) is False
    assert asyncio.run(is_subject_revoked("admin", "1", 1, None)) is False


def test_revocation_fails_open_on_redis_error():
    """Redis 故障时撤销读写都不抛异常：账号删除/停用仍由库内校验兜底。"""
    assert asyncio.run(is_subject_revoked("admin", "1", 1, _BrokenRedis())) is False
    asyncio.run(revoke_subject("admin", "1", _BrokenRedis()))


def test_revoke_subject_noop_without_redis():
    """Redis 未初始化时撤销为 no-op。"""
    asyncio.run(revoke_subject("admin", "1", None))
    assert revocation_key("member", "7") == "revoke:member:7"
