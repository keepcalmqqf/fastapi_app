MEMBER_PAYLOAD = {
    "nickname": "小明",
    "email": "xiaoming@test.com",
    "password": "123456",
}

ADMIN_PAYLOAD = {
    "name": "张三",
    "email": "zhangsan@test.com",
    "password": "123456",
    "is_active": True,
}


def _register_and_login(client) -> str:
    client.post("/member/register", json=MEMBER_PAYLOAD)
    resp = client.post(
        "/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    return resp.json()["data"]["access_token"]


def test_register_member(client):
    resp = client.post("/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["email"] == MEMBER_PAYLOAD["email"]
    assert "password" not in body["data"]


def test_register_member_duplicate_email(client):
    client.post("/member/register", json=MEMBER_PAYLOAD)
    resp = client.post("/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 409
    assert resp.json()["code"] == 409


def test_register_member_validation_error(client):
    resp = client.post("/member/register", json={**MEMBER_PAYLOAD, "nickname": "小"})
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_member_password_is_hashed(client):
    from sqlalchemy import select

    from app.models import Member
    from tests.conftest import TestingSessionLocal

    client.post("/member/register", json=MEMBER_PAYLOAD)
    with TestingSessionLocal() as db:
        member = db.scalar(select(Member).where(Member.email == MEMBER_PAYLOAD["email"]))
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


def test_member_me_without_token(client):
    resp = client.get("/member/me")
    assert resp.status_code == 401


def test_member_token_cannot_access_admin_api(client):
    token = _register_and_login(client)
    resp = client.get("/user/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_admin_token_cannot_access_member_api(client):
    client.post("/user/create_user", json=ADMIN_PAYLOAD)
    resp = client.post(
        "/auth/login",
        json={"email": ADMIN_PAYLOAD["email"], "password": ADMIN_PAYLOAD["password"]},
    )
    token = resp.json()["data"]["access_token"]
    resp = client.get("/member/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
