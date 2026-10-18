from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models import Member
from tests.conftest import run_db

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
    resp = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 200, f"注册失败: {resp.text}"
    resp = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["data"]["access_token"]


def _member_login(client) -> dict:
    """登录会员并返回完整令牌对（access + refresh）。"""
    resp = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["data"]


def _create_admin_token(client) -> str:
    """空库自举创建首个管理员并登录，返回 admin token。"""
    resp = client.post("/api/v1/auth/bootstrap", json=ADMIN_PAYLOAD)
    assert resp.status_code == 200, f"创建首个管理员失败: {resp.text}"
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_PAYLOAD["email"], "password": ADMIN_PAYLOAD["password"]},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["data"]["access_token"]


def test_register_member(client):
    resp = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["email"] == MEMBER_PAYLOAD["email"]
    assert "password" not in data
    assert "created_at" in data and "updated_at" in data


def test_register_member_duplicate_email_does_not_reveal_existence(client):
    """重复注册不回 409：否则该端点就是个「某邮箱是否已注册」的匿名查询接口。

    断言要点是两次响应**完全一致**（状态码 + code + message + data），调用方拿不到
    任何区分信号；提示邮件由 mailer 负责投递，不影响响应。
    """
    first = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    second = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    assert second.status_code == 200
    body = second.json()
    assert body["code"] == 200
    assert body["data"] is None
    # 关键：响应里不能有任何字段暗示该邮箱已存在，否则枚举仍然成立
    assert "已注册" not in body["message"]
    assert "存在" not in body["message"]
    # 首次注册是成功的，data 有内容——说明上面的 data=None 是「不泄露」而非固定行为
    assert first.json()["data"]["email"] == MEMBER_PAYLOAD["email"]


def test_register_member_duplicate_email_can_be_revealed(client, monkeypatch):
    """显式开启 REGISTER_REVEAL_EXISTING_EMAIL 时恢复 409 语义。

    保留这个开关是因为部分业务需要前端即时报错；默认关闭（见上一条用例）。
    """
    monkeypatch.setattr(settings, "REGISTER_REVEAL_EXISTING_EMAIL", True)
    client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    resp = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 409
    assert resp.json()["code"] == 409


def test_register_member_validation_error(client):
    resp = client.post("/api/v1/member/register", json={**MEMBER_PAYLOAD, "nickname": "小"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422
    assert body["message"] == "请求参数校验失败"
    errors = body["data"]["errors"]
    assert any("nickname" in e["loc"] for e in errors)


def test_register_member_blank_nickname(client):
    resp = client.post("/api/v1/member/register", json={**MEMBER_PAYLOAD, "nickname": "   "})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("nickname" in e["loc"] for e in errors)


def test_register_member_weak_password(client):
    resp = client.post("/api/v1/member/register", json={**MEMBER_PAYLOAD, "password": "12345678"})
    assert resp.status_code == 422
    errors = resp.json()["data"]["errors"]
    assert any("password" in e["loc"] for e in errors)


def test_register_member_email_normalized(client):
    payload = {**MEMBER_PAYLOAD, "email": "  XIAOMING@Test.COM "}
    resp = client.post("/api/v1/member/register", json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "xiaoming@test.com"


def test_member_password_is_hashed(client):
    client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    member = run_db(
        lambda db: db.scalar(select(Member).where(Member.email == MEMBER_PAYLOAD["email"]))
    )
    assert member is not None
    assert member.password != MEMBER_PAYLOAD["password"]
    assert member.password.startswith("$argon2")


def test_member_login_and_me(client):
    token = _register_and_login(client)
    me = client.get("/api/v1/member/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["email"] == MEMBER_PAYLOAD["email"]


def test_member_login_wrong_password(client):
    client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    resp = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_member_login_disabled(client):
    """禁用会员（is_active=False）登录返回 401。"""
    client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)

    async def _disable(db: AsyncSession) -> None:
        member = await db.scalar(select(Member).where(Member.email == MEMBER_PAYLOAD["email"]))
        assert member is not None
        member.is_active = False
        await db.commit()

    run_db(_disable)
    resp = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert resp.status_code == 401


def test_member_me_without_token(client):
    resp = client.get("/api/v1/member/me")
    assert resp.status_code == 401


def test_member_token_cannot_access_admin_api(client):
    """aud=member 的令牌访问后台接口返回 401。"""
    token = _register_and_login(client)
    resp = client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_admin_token_cannot_access_member_api(client):
    """aud=admin 的令牌访问会员接口返回 401。"""
    token = _create_admin_token(client)
    resp = client.get("/api/v1/member/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_member_refresh_token_rejected_as_access_token(client):
    """回归：会员 refresh 令牌不能作为 Bearer 访问会员接口（type 白名单校验）。"""
    resp = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert resp.status_code == 200, resp.text
    refresh_token = resp.json()["data"]["refresh_token"]

    assert (
        client.get(
            "/api/v1/member/me", headers={"Authorization": f"Bearer {refresh_token}"}
        ).status_code
        == 401
    )
    assert (
        client.patch(
            "/api/v1/member/me",
            json={"nickname": "明明"},
            headers={"Authorization": f"Bearer {refresh_token}"},
        ).status_code
        == 401
    )


# ---------- 会员资料更新 / 注销 ----------


def test_member_update_me(client):
    token = _register_and_login(client)
    resp = client.patch(
        "/api/v1/member/me",
        json={"nickname": "明明"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["nickname"] == "明明"

    me = client.get("/api/v1/member/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["data"]["nickname"] == "明明"


def test_member_delete_me_revokes_token_and_blocks_login(client):
    """注销（软删）后：原 token 访问 401，同邮箱再登录 401。"""
    token = _register_and_login(client)
    resp = client.delete("/api/v1/member/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200

    me = client.get("/api/v1/member/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401

    login = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert login.status_code == 401


def test_member_reregister_after_delete_conflicts(client, monkeypatch):
    """软删除不释放邮箱唯一约束：同邮箱再注册不会创建出第二个账号。

    默认配置下接口返回统一文案（不泄露邮箱已存在），故这里显式打开
    REGISTER_REVEAL_EXISTING_EMAIL 拿到可断言的 409；真正要守住的是
    「不会凭空多出一个会员」，用列表总数来断言。
    """
    monkeypatch.setattr(settings, "REGISTER_REVEAL_EXISTING_EMAIL", True)
    token = _register_and_login(client)
    client.delete("/api/v1/member/me", headers={"Authorization": f"Bearer {token}"})
    resp = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 409

    async def _count_by_email(db: AsyncSession) -> int:
        result = await db.execute(select(Member).where(Member.email == MEMBER_PAYLOAD["email"]))
        return len(result.scalars().all())

    assert run_db(_count_by_email) == 1


def test_member_delete_revokes_other_device_tokens(client):
    """注销会员会终止其全部会话：其他设备持有的 refresh 令牌同样失效。

    逐 jti 的 revoke_tokens 只能删掉发起注销那台设备的 refresh 令牌，
    多设备场景由主体级撤销标记覆盖，与后台软删用户的策略保持一致。
    """
    resp = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 200, resp.text

    device_a = _member_login(client)
    device_b = _member_login(client)
    assert device_a["refresh_token"] != device_b["refresh_token"]

    resp = client.delete(
        "/api/v1/member/me", headers={"Authorization": f"Bearer {device_a['access_token']}"}
    )
    assert resp.status_code == 200

    client.cookies.clear()
    resp = client.post("/api/v1/member/refresh", json={"refresh_token": device_b["refresh_token"]})
    assert resp.status_code == 401


# ---------- 会员 refresh / logout ----------


def test_member_refresh_rotates_and_rejects_reuse(client):
    _register_and_login(client)
    old_refresh = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    ).json()["data"]["refresh_token"]

    resp = client.post("/api/v1/member/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["refresh_token"] != old_refresh

    # 旧 refresh jti 已删除，重用即 401（先清 Cookie jar，避免有效 Cookie 优先于 body）
    client.cookies.clear()
    resp = client.post("/api/v1/member/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 401


def test_member_logout_revokes_tokens(client):
    """member logout 后：旧 access token 失效，旧 refresh token 被撤销。"""
    access = _register_and_login(client)
    resp = client.post("/api/v1/member/logout", headers={"Authorization": f"Bearer {access}"})
    assert resp.status_code == 200

    me = client.get("/api/v1/member/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 401

    tokens = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    ).json()["data"]
    resp = client.post("/api/v1/member/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    new_access = resp.json()["data"]["access_token"]
    client.post("/api/v1/member/logout", headers={"Authorization": f"Bearer {new_access}"})
    client.cookies.clear()  # 避免 Cookie jar 中更新后的 refresh Cookie 优先于 body
    resp = client.post("/api/v1/member/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


# ---------- 会员列表（后台分页） ----------


def test_member_list_pagination(client):
    """/api/v1/member/ 需 admin 令牌，返回 Page 结构；page_size>100 返回 422。"""
    for i in range(3):
        resp = client.post(
            "/api/v1/member/register",
            json={
                "nickname": f"会员{i}",
                "email": f"member{i}@test.com",
                "password": "abcd1234",
            },
        )
        assert resp.status_code == 200, resp.text

    admin_token = _create_admin_token(client)
    resp = client.get("/api/v1/member/", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 3
    assert data["page_size"] == 20
    assert len(data["items"]) == 3

    resp = client.get(
        "/api/v1/member/",
        params={"page_size": 101},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


def test_member_list_requires_admin_token(client):
    """会员令牌访问 /api/v1/member/ 返回 401（aud 不匹配）。"""
    member_token = _register_and_login(client)
    resp = client.get("/api/v1/member/", headers={"Authorization": f"Bearer {member_token}"})
    assert resp.status_code == 401


# ---------- 修改密码 ----------

_NEW_PASSWORD = "newpass123"


def _change_password(client, token: str, old_password: str, new_password: str):
    return client.post(
        "/api/v1/member/change-password",
        json={"old_password": old_password, "new_password": new_password},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_member_change_password_success(client):
    """会员改密：旧密码失效、新密码可登录。"""
    token = _register_and_login(client)
    resp = _change_password(client, token, MEMBER_PAYLOAD["password"], _NEW_PASSWORD)
    assert resp.status_code == 200

    old_login = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert old_login.status_code == 401
    new_login = client.post(
        "/api/v1/member/login", json={"email": MEMBER_PAYLOAD["email"], "password": _NEW_PASSWORD}
    )
    assert new_login.status_code == 200


def test_member_change_password_wrong_old_password(client):
    """原密码错误返回 401，密码未被改动。"""
    token = _register_and_login(client)
    resp = _change_password(client, token, "wrong-password", _NEW_PASSWORD)
    assert resp.status_code == 401

    assert (
        client.post(
            "/api/v1/member/login",
            json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
        ).status_code
        == 200
    )


def test_member_change_password_revokes_sessions(client):
    """改密终止该会员全部会话：改动前签发的令牌立即失效。"""
    token = _register_and_login(client)
    assert (
        client.get("/api/v1/member/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )

    assert (
        _change_password(client, token, MEMBER_PAYLOAD["password"], _NEW_PASSWORD).status_code
        == 200
    )
    assert (
        client.get("/api/v1/member/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )
