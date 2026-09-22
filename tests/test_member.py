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


# ---------- 会员资料更新 / 注销 ----------


def test_member_update_me(client):
    token = _register_and_login(client)
    resp = client.patch(
        "/member/me",
        json={"nickname": "明明"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["nickname"] == "明明"

    me = client.get("/member/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["data"]["nickname"] == "明明"


def test_member_delete_me_revokes_token_and_blocks_login(client):
    """注销（软删）后：原 token 访问 401，同邮箱再登录 401。"""
    token = _register_and_login(client)
    resp = client.delete("/member/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200

    me = client.get("/member/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401

    login = client.post(
        "/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert login.status_code == 401


def test_member_reregister_after_delete_conflicts(client):
    """软删除不释放邮箱唯一约束：同邮箱再注册返回 409。"""
    token = _register_and_login(client)
    client.delete("/member/me", headers={"Authorization": f"Bearer {token}"})
    resp = client.post("/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 409


# ---------- 会员 refresh / logout ----------


def test_member_refresh_rotates_and_rejects_reuse(client):
    _register_and_login(client)
    old_refresh = client.post(
        "/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    ).json()["data"]["refresh_token"]

    resp = client.post("/member/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["refresh_token"] != old_refresh

    # 旧 refresh jti 已删除，重用即 401（先清 Cookie jar，避免有效 Cookie 优先于 body）
    client.cookies.clear()
    resp = client.post("/member/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 401


def test_member_logout_revokes_tokens(client):
    """member logout 后：旧 access token 失效，旧 refresh token 被撤销。"""
    access = _register_and_login(client)
    resp = client.post("/member/logout", headers={"Authorization": f"Bearer {access}"})
    assert resp.status_code == 200

    me = client.get("/member/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 401

    tokens = client.post(
        "/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    ).json()["data"]
    resp = client.post("/member/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    new_access = resp.json()["data"]["access_token"]
    client.post("/member/logout", headers={"Authorization": f"Bearer {new_access}"})
    client.cookies.clear()  # 避免 Cookie jar 中更新后的 refresh Cookie 优先于 body
    resp = client.post("/member/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


# ---------- 会员列表（后台分页） ----------


def test_member_list_pagination(client):
    """/member/list 需 admin 令牌，返回 Page 结构；page_size>100 返回 422。"""
    for i in range(3):
        resp = client.post(
            "/member/register",
            json={
                "nickname": f"会员{i}",
                "email": f"member{i}@test.com",
                "password": "abcd1234",
            },
        )
        assert resp.status_code == 200, resp.text

    admin_token = _create_admin_token(client)
    resp = client.get("/member/list", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 3
    assert data["page_size"] == 20
    assert len(data["items"]) == 3

    resp = client.get(
        "/member/list",
        params={"page_size": 101},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


def test_member_list_requires_admin_token(client):
    """会员令牌访问 /member/list 返回 401（aud 不匹配）。"""
    member_token = _register_and_login(client)
    resp = client.get("/member/list", headers={"Authorization": f"Bearer {member_token}"})
    assert resp.status_code == 401
