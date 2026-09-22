from typing import Any

import jwt

from app.core.security import create_access_token
from app.core.settings import settings
from tests.conftest import TestingSessionLocal

USER_PAYLOAD: dict[str, Any] = {
    "name": "张三",
    "email": "zhangsan@test.com",
    "password": "abcd1234",
    "is_active": True,
}


def _login(client, email: str, password: str) -> str:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["data"]["access_token"]


def _create_first_admin(client, payload: dict | None = None) -> str:
    """空库自举创建首个管理员并登录，返回 admin token。"""
    payload = payload or USER_PAYLOAD
    resp = client.post("/user/create_user", json=payload)
    assert resp.status_code == 200, f"创建首个管理员失败: {resp.text}"
    return _login(client, payload["email"], payload["password"])


def test_create_user_bootstrap_without_token(client):
    """空库自举：users 表为空时无 token 可创建首个管理员。"""
    resp = client.post("/user/create_user", json=USER_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["email"] == USER_PAYLOAD["email"]
    assert "password" not in data
    assert "created_at" in data and "updated_at" in data


def test_create_user_requires_token_when_table_not_empty(client):
    """表非空时无 token 创建用户返回 401。"""
    client.post("/user/create_user", json=USER_PAYLOAD)
    resp = client.post(
        "/user/create_user",
        json={**USER_PAYLOAD, "email": "lisi@test.com"},
    )
    assert resp.status_code == 401


def test_create_user_with_admin_token(client):
    token = _create_first_admin(client)
    resp = client.post(
        "/user/create_user",
        json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "lisi@test.com"


def test_create_user_duplicate_email(client):
    token = _create_first_admin(client)
    resp = client.post(
        "/user/create_user",
        json=USER_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == 409


def test_create_user_validation_error(client):
    resp = client.post("/user/create_user", json={**USER_PAYLOAD, "name": "张"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422
    assert body["message"] == "请求参数校验失败"
    errors = body["data"]["errors"]
    assert any("name" in e["loc"] for e in errors)


def test_create_user_weak_password(client):
    """纯数字密码不满足「字母+数字」策略，返回 422。"""
    resp = client.post("/user/create_user", json={**USER_PAYLOAD, "password": "12345678"})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("password" in e["loc"] for e in errors)


def test_create_user_short_password(client):
    """少于 8 位密码返回 422。"""
    resp = client.post("/user/create_user", json={**USER_PAYLOAD, "password": "abc123"})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("password" in e["loc"] for e in errors)


def test_create_user_blank_name(client):
    """纯空白 name 返回 422。"""
    resp = client.post("/user/create_user", json={**USER_PAYLOAD, "name": "   "})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("name" in e["loc"] for e in errors)


def test_email_normalized(client):
    """大写/带空白邮箱注册后归一化为小写，用小写登录成功。"""
    payload = {**USER_PAYLOAD, "email": "  ZHANGSAN@Test.COM  "}
    resp = client.post("/user/create_user", json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "zhangsan@test.com"

    token = _login(client, "zhangsan@test.com", USER_PAYLOAD["password"])
    me = client.get("/user/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["email"] == "zhangsan@test.com"


def test_get_user(client):
    token = _create_first_admin(client)
    created_resp = client.post(
        "/user/create_user",
        json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created_resp.status_code == 200, created_resp.text
    created = created_resp.json()["data"]
    resp = client.get(
        "/user/get_user",
        params={"user_id": created["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == created["id"]


def test_get_user_without_token(client):
    client.post("/user/create_user", json=USER_PAYLOAD)
    resp = client.get("/user/get_user", params={"user_id": 1})
    assert resp.status_code == 401


def test_get_user_not_found(client):
    token = _create_first_admin(client)
    resp = client.get(
        "/user/get_user",
        params={"user_id": 99999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] is None


def test_password_is_hashed(client):
    from sqlalchemy import select

    from app.models import User

    client.post("/user/create_user", json=USER_PAYLOAD)
    with TestingSessionLocal() as db:
        user = db.scalar(select(User).where(User.email == USER_PAYLOAD["email"]))
    assert user is not None
    assert user.password != USER_PAYLOAD["password"]
    assert user.password.startswith("$argon2")


def test_login_and_me(client):
    token = _create_first_admin(client)
    me = client.get("/user/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["email"] == USER_PAYLOAD["email"]


def test_login_wrong_password(client):
    client.post("/user/create_user", json=USER_PAYLOAD)
    resp = client.post(
        "/auth/login",
        json={"email": USER_PAYLOAD["email"], "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_login_disabled_user(client):
    """禁用账号（is_active=False）登录返回 401。"""
    client.post("/user/create_user", json={**USER_PAYLOAD, "is_active": False})
    resp = client.post(
        "/auth/login",
        json={"email": USER_PAYLOAD["email"], "password": USER_PAYLOAD["password"]},
    )
    assert resp.status_code == 401


def test_me_without_token(client):
    resp = client.get("/user/me")
    assert resp.status_code == 401


def test_expired_token_rejected(client):
    _create_first_admin(client)
    expired = create_access_token(subject="1", expires_minutes=-1)
    resp = client.get("/user/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_forged_token_rejected(client):
    _create_first_admin(client)
    forged = jwt.encode(
        {"sub": "1", "aud": "admin"},
        "not-the-real-secret-key",
        algorithm=settings.JWT_ALGORITHM,
    )
    resp = client.get("/user/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401
