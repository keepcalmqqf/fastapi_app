from sqlalchemy import select

from app.models import Member
from tests.conftest import TestingSessionLocal

MEMBER_PAYLOAD = {
    "nickname": "小明",
    "email": "xiaoming@test.com",
    "password": "abcd1234",
}

ADMIN_PAYLOAD = {
    "name": "张三",
    "email": "zhangsan@test.com",
    "password": "abcd1234",
    "is_active": True,
}


def _register_and_login(client) -> str:
    resp = client.post("/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 200, f"注册失败: {resp.text}"
    resp = client.post(
        "/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["data"]["access_token"]


def _create_admin_token(client) -> str:
    """空库自举创建首个管理员并登录，返回 admin token。"""
    resp = client.post("/user/create_user", json=ADMIN_PAYLOAD)
    assert resp.status_code == 200, f"创建首个管理员失败: {resp.text}"
    resp = client.post(
        "/auth/login",
        json={"email": ADMIN_PAYLOAD["email"], "password": ADMIN_PAYLOAD["password"]},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["data"]["access_token"]


def test_register_member(client):
    resp = client.post("/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["email"] == MEMBER_PAYLOAD["email"]
    assert "password" not in data
    assert "created_at" in data and "updated_at" in data


def test_register_member_duplicate_email(client):
    client.post("/member/register", json=MEMBER_PAYLOAD)
    resp = client.post("/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 409
    assert resp.json()["code"] == 409


def test_register_member_validation_error(client):
    resp = client.post("/member/register", json={**MEMBER_PAYLOAD, "nickname": "小"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422
    assert body["message"] == "请求参数校验失败"
    errors = body["data"]["errors"]
    assert any("nickname" in e["loc"] for e in errors)


def test_register_member_blank_nickname(client):
    resp = client.post("/member/register", json={**MEMBER_PAYLOAD, "nickname": "   "})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("nickname" in e["loc"] for e in errors)


def test_register_member_weak_password(client):
    resp = client.post("/member/register", json={**MEMBER_PAYLOAD, "password": "12345678"})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("password" in e["loc"] for e in errors)


def test_register_member_email_normalized(client):
    payload = {**MEMBER_PAYLOAD, "email": "  XIAOMING@Test.COM "}
    resp = client.post("/member/register", json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "xiaoming@test.com"


def test_member_password_is_hashed(client):
    client.post("/member/register", json=MEMBER_PAYLOAD)
    with TestingSessionLocal() as db:
        member = db.scalar(select(Member).where(Member.email == MEMBER_PAYLOAD["email"]))
    assert member is not None
    assert member.password != MEMBER_PAYLOAD["password"]
    assert member.password.startswith("$argon2")


def test_member_login_and_me(client):
    token = _register_and_login(client)
    me = client.get("/member/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["email"] == MEMBER_PAYLOAD["email"]


def test_member_login_wrong_password(client):
    client.post("/member/register", json=MEMBER_PAYLOAD)
    resp = client.post(
        "/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_member_login_disabled(client):
    """禁用会员（is_active=False）登录返回 401。"""
    client.post("/member/register", json=MEMBER_PAYLOAD)
    with TestingSessionLocal() as db:
        member = db.scalar(select(Member).where(Member.email == MEMBER_PAYLOAD["email"]))
        assert member is not None
        member.is_active = False
        db.commit()
    resp = client.post(
        "/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert resp.status_code == 401


def test_member_me_without_token(client):
    resp = client.get("/member/me")
    assert resp.status_code == 401


def test_member_token_cannot_access_admin_api(client):
    """aud=member 的令牌访问后台接口返回 401。"""
    token = _register_and_login(client)
    resp = client.get("/user/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_admin_token_cannot_access_member_api(client):
    """aud=admin 的令牌访问会员接口返回 401。"""
    token = _create_admin_token(client)
    resp = client.get("/member/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
