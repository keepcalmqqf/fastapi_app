from typing import Any

import jwt

from app.core.security import create_access_token
from app.core.settings import Settings, settings
from tests.conftest import run_db

USER_PAYLOAD: dict[str, Any] = {
    "name": "张三",
    "email": "zhangsan@test.com",
    "password": "abcd1234",
    "is_active": True,
}


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["data"]["access_token"]


def _create_first_admin(client, payload: dict | None = None) -> str:
    """空库自举创建首个管理员并登录，返回 admin token。"""
    payload = payload or USER_PAYLOAD
    resp = client.post("/api/v1/auth/bootstrap", json=payload)
    assert resp.status_code == 200, f"创建首个管理员失败: {resp.text}"
    return _login(client, payload["email"], payload["password"])


def test_create_user_bootstrap_without_token(client):
    """空库自举：users 表为空时无 token 可创建首个管理员。"""
    resp = client.post("/api/v1/auth/bootstrap", json=USER_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["email"] == USER_PAYLOAD["email"]
    assert "password" not in data
    assert "created_at" in data and "updated_at" in data


def test_create_user_requires_token_when_table_not_empty(client):
    """表非空时无 token 创建用户返回 401。"""
    client.post("/api/v1/auth/bootstrap", json=USER_PAYLOAD)
    resp = client.post(
        "/api/v1/user/",
        json={**USER_PAYLOAD, "email": "lisi@test.com"},
    )
    assert resp.status_code == 401


def test_create_user_with_admin_token(client):
    token = _create_first_admin(client)
    resp = client.post(
        "/api/v1/user/",
        json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "lisi@test.com"


def test_create_user_duplicate_email(client):
    token = _create_first_admin(client)
    resp = client.post(
        "/api/v1/user/",
        json=USER_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == 409


def test_create_user_validation_error(client):
    resp = client.post("/api/v1/auth/bootstrap", json={**USER_PAYLOAD, "name": "张"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422
    assert body["message"] == "请求参数校验失败"
    errors = body["data"]["errors"]
    assert any("name" in e["loc"] for e in errors)


def test_create_user_weak_password(client):
    """纯数字密码不满足「字母+数字」策略，返回 422。"""
    resp = client.post("/api/v1/auth/bootstrap", json={**USER_PAYLOAD, "password": "12345678"})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("password" in e["loc"] for e in errors)


def test_create_user_short_password(client):
    """少于 8 位密码返回 422。"""
    resp = client.post("/api/v1/auth/bootstrap", json={**USER_PAYLOAD, "password": "abc123"})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("password" in e["loc"] for e in errors)


def test_create_user_blank_name(client):
    """纯空白 name 返回 422。"""
    resp = client.post("/api/v1/auth/bootstrap", json={**USER_PAYLOAD, "name": "   "})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("name" in e["loc"] for e in errors)


def test_email_normalized(client):
    """大写/带空白邮箱注册后归一化为小写，用小写登录成功。"""
    payload = {**USER_PAYLOAD, "email": "  ZHANGSAN@Test.COM  "}
    resp = client.post("/api/v1/auth/bootstrap", json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "zhangsan@test.com"

    token = _login(client, "zhangsan@test.com", USER_PAYLOAD["password"])
    me = client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["email"] == "zhangsan@test.com"


def test_get_user(client):
    token = _create_first_admin(client)
    created_resp = client.post(
        "/api/v1/user/",
        json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created_resp.status_code == 200, created_resp.text
    created = created_resp.json()["data"]
    resp = client.get(
        f"/api/v1/user/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == created["id"]


# ---------- REST 风格路由（/api/v1/user/、/api/v1/user/{user_id}） ----------


def test_rest_routes_for_user_crud(client):
    """REST 风格路径可用：POST /api/v1/user/ 创建、GET /api/v1/user/ 列表与详情。"""
    token = _create_first_admin(client)
    auth = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/api/v1/user/",
        json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"},
        headers=auth,
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["data"]["id"]

    listed = client.get("/api/v1/user/", headers=auth)
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 2

    fetched = client.get(f"/api/v1/user/{user_id}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["data"]["id"] == user_id


def test_legacy_paths_removed(client):
    """旧 RPC 路径已彻底移除，不再返回 200。

    它们现在都落到 /api/v1/user/{user_id} 上：方法不对的返回 405，
    方法对但路径段无法转 int 的返回 422。
    """
    token = _create_first_admin(client)
    auth = {"Authorization": f"Bearer {token}"}

    legacy_create = "/api/v1/user/" + "create_user"
    legacy_list = "/api/v1/user/" + "list"
    legacy_get = "/api/v1/user/" + "get_user"
    assert client.post(legacy_create, json=USER_PAYLOAD, headers=auth).status_code == 405
    assert client.get(legacy_list, headers=auth).status_code == 422
    assert client.get(legacy_get, params={"user_id": 1}, headers=auth).status_code == 422


def test_rest_get_user_invalid_id_returns_422(client):
    """/api/v1/user/{user_id} 的 user_id 非数字时返回统一格式的 422。"""
    token = _create_first_admin(client)
    resp = client.get("/api/v1/user/not-a-number", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_rest_route_requires_token_when_table_not_empty(client):
    """创建后台用户恒需令牌：表非空时无令牌返回 401。"""
    client.post("/api/v1/auth/bootstrap", json=USER_PAYLOAD)
    resp = client.post("/api/v1/user/", json={**USER_PAYLOAD, "email": "lisi@test.com"})
    assert resp.status_code == 401


def test_get_user_without_token(client):
    client.post("/api/v1/auth/bootstrap", json=USER_PAYLOAD)
    resp = client.get("/api/v1/user/1")
    assert resp.status_code == 401


def test_get_user_not_found(client):
    token = _create_first_admin(client)
    resp = client.get(
        "/api/v1/user/99999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


def test_password_is_hashed(client):
    from sqlalchemy import select

    from app.models import User

    client.post("/api/v1/auth/bootstrap", json=USER_PAYLOAD)
    user = run_db(lambda db: db.scalar(select(User).where(User.email == USER_PAYLOAD["email"])))
    assert user is not None
    assert user.password != USER_PAYLOAD["password"]
    assert user.password.startswith("$argon2")


def test_login_and_me(client):
    token = _create_first_admin(client)
    me = client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["email"] == USER_PAYLOAD["email"]


def test_login_wrong_password(client):
    client.post("/api/v1/auth/bootstrap", json=USER_PAYLOAD)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": USER_PAYLOAD["email"], "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_login_disabled_user(client):
    """禁用账号（is_active=False）登录返回 401。"""
    client.post("/api/v1/auth/bootstrap", json={**USER_PAYLOAD, "is_active": False})
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": USER_PAYLOAD["email"], "password": USER_PAYLOAD["password"]},
    )
    assert resp.status_code == 401


def test_me_without_token(client):
    resp = client.get("/api/v1/user/me")
    assert resp.status_code == 401


def test_expired_token_rejected(client):
    _create_first_admin(client)
    expired = create_access_token(subject="1", aud="admin", expires_minutes=-1)
    resp = client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_forged_token_rejected(client):
    _create_first_admin(client)
    forged = jwt.encode(
        {"sub": "1", "aud": "admin"},
        # 密钥长度取 >=32 字节，避免 PyJWT 对短 HMAC 密钥发出 InsecureKeyLengthWarning
        "not-the-real-secret-key-but-long-enough-to-be-safe",
        algorithm=settings.JWT_ALGORITHM,
    )
    resp = client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


# ---------- 分页 ----------


def _create_users(client, token: str, count: int) -> None:
    for i in range(count):
        resp = client.post(
            "/api/v1/user/",
            json={
                "name": f"用户{i}",
                "email": f"user{i}@test.com",
                "password": "abcd1234",
                "is_active": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text


def test_list_users_pagination(client):
    """/api/v1/user/ 返回 Page 结构：total/items/page/page_size 正确，分页生效。"""
    token = _create_first_admin(client)
    _create_users(client, token, 3)  # 共 4 个用户

    resp = client.get("/api/v1/user/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 4
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert len(data["items"]) == 4

    resp = client.get(
        "/api/v1/user/",
        params={"page": 2, "page_size": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 4
    assert data["page"] == 2
    assert data["page_size"] == 3
    assert len(data["items"]) == 1


def test_list_users_page_size_bounds(client):
    """page_size 上限 100，越界 422；page 从 1 开始。"""
    token = _create_first_admin(client)
    resp = client.get(
        "/api/v1/user/", params={"page_size": 101}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422
    resp = client.get(
        "/api/v1/user/", params={"page": 0}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422


def test_list_users_page_upper_bound(client):
    """page 同样设上限：无上限时超大页码等价于一次全表扫描。"""
    token = _create_first_admin(client)
    resp = client.get(
        "/api/v1/user/", params={"page": 10001}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422


def test_list_users_requires_admin(client):
    _create_first_admin(client)
    client.cookies.clear()  # 不带任何凭证（Cookie jar 会记住登录种下的 Cookie）
    resp = client.get("/api/v1/user/")
    assert resp.status_code == 401


# ---------- PATCH / DELETE ----------


def _create_one_user(client, token: str, email: str = "lisi@test.com") -> dict:
    resp = client.post(
        "/api/v1/user/",
        json={"name": "李四", "email": email, "password": "abcd1234", "is_active": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_update_user_name(client):
    token = _create_first_admin(client)
    user = _create_one_user(client, token)
    resp = client.patch(
        f"/api/v1/user/{user['id']}",
        json={"name": "李小四"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "李小四"


def test_update_user_disable_blocks_login(client):
    """停用用户后其登录返回 401。"""
    token = _create_first_admin(client)
    user = _create_one_user(client, token)
    resp = client.patch(
        f"/api/v1/user/{user['id']}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["is_active"] is False

    login = client.post(
        "/api/v1/auth/login", json={"email": "lisi@test.com", "password": "abcd1234"}
    )
    assert login.status_code == 401


def test_update_user_not_found(client):
    token = _create_first_admin(client)
    resp = client.patch(
        "/api/v1/user/99999", json={"name": "不存在"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404


def test_delete_user_soft_blocks_login_and_lookup(client):
    """删除用户（软删）后：该用户登录 401，get_user 404。"""
    token = _create_first_admin(client)
    user = _create_one_user(client, token)

    resp = client.delete(f"/api/v1/user/{user['id']}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200

    login = client.post(
        "/api/v1/auth/login", json={"email": "lisi@test.com", "password": "abcd1234"}
    )
    assert login.status_code == 401

    resp = client.get(
        f"/api/v1/user/{user['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_delete_user_not_found(client):
    token = _create_first_admin(client)
    resp = client.delete("/api/v1/user/99999", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_delete_self_forbidden(client):
    token = _create_first_admin(client)
    me = client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {token}"})
    resp = client.delete(
        f"/api/v1/user/{me.json()['data']['id']}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 400


def test_disable_self_forbidden(client):
    """不允许把自己停用：把自己置为 is_active=False 会把全员锁在门外且无法自助恢复。"""
    token = _create_first_admin(client)
    me = client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {token}"})
    me_id = me.json()["data"]["id"]
    resp = client.patch(
        f"/api/v1/user/{me_id}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 400
    # 账号仍处于启用状态
    still = client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {token}"})
    assert still.json()["data"]["is_active"] is True


def test_disable_self_with_other_fields_still_allowed(client):
    """反向保障：改自己的昵称不受「禁止自我停用」影响。"""
    token = _create_first_admin(client)
    me = client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {token}"}).json()["data"]
    resp = client.patch(
        f"/api/v1/user/{me['id']}",
        json={"name": "新名字"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "新名字"


def test_bootstrap_can_be_disabled(client, monkeypatch):
    """ALLOW_BOOTSTRAP 关闭后自举端点直接拒绝（PROD 默认关闭），且无法通过建号绕过。"""
    monkeypatch.setattr(Settings, "bootstrap_allowed", False)
    resp = client.post("/api/v1/auth/bootstrap", json=USER_PAYLOAD)
    assert resp.status_code == 403

    # 自举关闭后 /user/ 仍要求令牌：两者分离，避免端点语义混用留下免鉴权口子
    assert client.post("/api/v1/user/", json=USER_PAYLOAD).status_code == 401


def test_timestamps_come_from_single_utc_source(client):
    """审计时间戳与软删时间统一由应用侧生成（naive UTC），不与数据库时区错位。

    数据库侧 func.now() 取服务器时区，MySQL 跑在本地时区时会与 Python UTC 差若干小时，
    按时间范围过滤软删记录即出错。
    """
    from sqlalchemy import select

    from app.models import User
    from app.models.base import utc_now

    token = _create_first_admin(client)
    user = _create_one_user(client, token)
    assert (
        client.delete(
            f"/api/v1/user/{user['id']}", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 200
    )

    row = run_db(lambda db: db.scalar(select(User).where(User.id == user["id"])))
    assert row is not None
    for ts in (row.created_at, row.updated_at, row.deleted_at):
        assert ts is not None
        assert ts.tzinfo is None
    assert row.deleted_at is not None
    assert row.deleted_at >= row.created_at
    # 与当前 UTC 的偏差在分钟级：若时间仍由数据库本地时区生成，这里会差若干小时
    assert abs((utc_now() - row.deleted_at).total_seconds()) < 300


# ---------- 账号级令牌撤销（软删 / 停用 / 重新启用） ----------


def _login_tokens(client, email: str, password: str) -> dict:
    """登录并返回完整令牌对（access + refresh）。"""
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_delete_user_revokes_all_tokens(client):
    """软删用户后其全部令牌失效：access 401，refresh 也不能再换发新令牌。

    修复前 refresh 仍可轮换（200），登出/删除都无法真正终止该账号会话。
    """
    admin_token = _create_first_admin(client)
    victim = _create_one_user(client, admin_token)
    victim_tokens = _login_tokens(client, "lisi@test.com", "abcd1234")

    resp = client.delete(
        f"/api/v1/user/{victim['id']}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200

    headers = {"Authorization": f"Bearer {victim_tokens['access_token']}"}
    assert client.get("/api/v1/user/me", headers=headers).status_code == 401

    client.cookies.clear()  # 避免 Cookie 优先，确保校验的是 body 里的 refresh 令牌
    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": victim_tokens["refresh_token"]}
    )
    assert resp.status_code == 401


def test_disable_user_revokes_refresh_token(client):
    """停用用户同样终止其会话：旧 refresh 令牌不能再换发新令牌。"""
    admin_token = _create_first_admin(client)
    victim = _create_one_user(client, admin_token)
    victim_tokens = _login_tokens(client, "lisi@test.com", "abcd1234")

    resp = client.patch(
        f"/api/v1/user/{victim['id']}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["is_active"] is False

    client.cookies.clear()
    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": victim_tokens["refresh_token"]}
    )
    assert resp.status_code == 401


def test_update_user_name_does_not_revoke_tokens(client):
    """反向保障：仅改昵称不应触发任何令牌撤销。"""
    admin_token = _create_first_admin(client)
    victim = _create_one_user(client, admin_token)
    victim_tokens = _login_tokens(client, "lisi@test.com", "abcd1234")

    resp = client.patch(
        f"/api/v1/user/{victim['id']}",
        json={"name": "李小四"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    client.cookies.clear()
    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": victim_tokens["refresh_token"]}
    )
    assert resp.status_code == 200, resp.text


def test_reenable_user_does_not_resurrect_old_tokens(client):
    """重新启用账号不会复活旧会话：停用前签发的 refresh 令牌保持失效。"""
    admin_token = _create_first_admin(client)
    victim = _create_one_user(client, admin_token)
    old_tokens = _login_tokens(client, "lisi@test.com", "abcd1234")

    auth = {"Authorization": f"Bearer {admin_token}"}
    assert (
        client.patch(
            f"/api/v1/user/{victim['id']}", json={"is_active": False}, headers=auth
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v1/user/{victim['id']}", json={"is_active": True}, headers=auth
        ).status_code
        == 200
    )

    client.cookies.clear()
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": old_tokens["refresh_token"]})
    assert resp.status_code == 401
