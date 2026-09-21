USER_PAYLOAD = {
    "name": "张三",
    "email": "zhangsan@test.com",
    "password": "123456",
    "is_active": True,
}


def test_create_user(client):
    resp = client.post("/user/create_user", json=USER_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["email"] == USER_PAYLOAD["email"]
    assert "password" not in body["data"]


def test_create_user_duplicate_email(client):
    client.post("/user/create_user", json=USER_PAYLOAD)
    resp = client.post("/user/create_user", json=USER_PAYLOAD)
    assert resp.status_code == 409
    assert resp.json()["code"] == 409


def test_create_user_validation_error(client):
    resp = client.post("/user/create_user", json={**USER_PAYLOAD, "name": "张"})
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_get_user(client):
    created = client.post("/user/create_user", json=USER_PAYLOAD).json()["data"]
    resp = client.get("/user/get_user", params={"user_id": created["id"]})
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == created["id"]


def test_get_user_not_found(client):
    resp = client.get("/user/get_user", params={"user_id": 99999})
    assert resp.status_code == 200
    assert resp.json()["data"] is None


def test_password_is_hashed(client):
    from sqlalchemy import select

    from app.models import User
    from tests.conftest import TestingSessionLocal

    client.post("/user/create_user", json=USER_PAYLOAD)
    with TestingSessionLocal() as db:
        user = db.scalar(select(User).where(User.email == USER_PAYLOAD["email"]))
    assert user.password != USER_PAYLOAD["password"]
    assert user.password.startswith("$argon2")


def test_login_and_me(client):
    client.post("/user/create_user", json=USER_PAYLOAD)
    resp = client.post(
        "/auth/login",
        json={"email": USER_PAYLOAD["email"], "password": USER_PAYLOAD["password"]},
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]

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


def test_me_without_token(client):
    resp = client.get("/user/me")
    assert resp.status_code == 401


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "components" in body["data"]
    assert set(body["data"]["components"]) == {"mysql", "redis"}
