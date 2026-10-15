"""审计日志测试：写操作留痕、登录失败留痕、查询鉴权、审计故障不影响业务。"""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.services.audit import (
    ACTION_CREATE_USER,
    ACTION_DELETE_MEMBER,
    ACTION_DELETE_USER,
    ACTION_LOGIN_FAILED,
    ACTION_UPDATE_USER,
)

from .conftest import run_db

USER_PAYLOAD: dict[str, Any] = {
    "name": "张三",
    "email": "zhangsan@test.com",
    "password": "abcd1234",
    "is_active": True,
}

MEMBER_PAYLOAD = {
    "nickname": "小明",
    "email": "xiaoming@test.com",
    "password": "abcd1234",
}


def _login(client) -> str:
    resp = client.post(
        "/auth/login", json={"email": USER_PAYLOAD["email"], "password": USER_PAYLOAD["password"]}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _admin_token(client) -> str:
    """空库自举创建管理员并登录，返回 admin 令牌。"""
    resp = client.post("/user/", json=USER_PAYLOAD)
    assert resp.status_code == 200, resp.text
    return _login(client)


def _me_id(client, token: str) -> int:
    resp = client.get("/user/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    return resp.json()["data"]["id"]


def _logs(client, token: str, **params) -> dict:
    resp = client.get("/audit/logs", params=params, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_create_user_audited_without_actor_on_bootstrap(client):
    """创建用户留痕；空库自举时没有操作者，actor_id 为 None。"""
    assert client.post("/user/", json=USER_PAYLOAD).status_code == 200
    token = _login(client)

    data = _logs(client, token)
    assert data["total"] == 1
    item = data["items"][0]
    assert item["action"] == ACTION_CREATE_USER
    assert item["target_type"] == "user"
    assert item["actor_type"] == "admin"
    assert item["actor_id"] is None


def test_create_user_by_admin_records_actor(client):
    """管理员创建用户时记录操作者 id。"""
    token = _admin_token(client)
    auth = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/user/", json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"}, headers=auth
    )
    assert created.status_code == 200, created.text

    # 两条 user.create：自举那条（actor 为空）+ 管理员这条
    data = _logs(client, token, action=ACTION_CREATE_USER)
    assert data["total"] == 2
    # 倒序，最新一条是管理员创建的，应带操作者
    assert data["items"][0]["actor_id"] == _me_id(client, token)


def test_update_and_delete_audited(client):
    """更新与删除用户都留痕，且记录操作者与变更要点。"""
    token = _admin_token(client)
    auth = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/user/", json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"}, headers=auth
    )
    assert created.status_code == 200
    user_id = created.json()["data"]["id"]

    assert (
        client.patch(f"/user/{user_id}", json={"name": "李小四"}, headers=auth).status_code == 200
    )
    assert client.delete(f"/user/{user_id}", headers=auth).status_code == 200

    data = _logs(client, token)
    # 按 id 倒序：最新在前
    assert data["items"][0]["action"] == ACTION_DELETE_USER
    assert data["items"][1]["action"] == ACTION_UPDATE_USER
    assert data["items"][0]["actor_id"] == _me_id(client, token)
    assert "李小四" in (data["items"][1]["detail"] or "")


def test_login_failure_audited(client):
    """登录失败留痕，便于复盘撞库（成功登录不记录，避免日志膨胀）。"""
    token = _admin_token(client)
    client.cookies.clear()

    bad = client.post(
        "/auth/login", json={"email": USER_PAYLOAD["email"], "password": "wrong-password"}
    )
    assert bad.status_code == 401

    data = _logs(client, token, action=ACTION_LOGIN_FAILED)
    assert data["total"] == 1
    assert data["items"][0]["target_id"] == USER_PAYLOAD["email"]


def test_member_delete_audited(client):
    """会员注销留痕，actor_type 为 member。"""
    assert client.post("/member/register", json=MEMBER_PAYLOAD).status_code == 200
    login = client.post(
        "/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert login.status_code == 200
    member_token = login.json()["data"]["access_token"]

    resp = client.delete("/member/me", headers={"Authorization": f"Bearer {member_token}"})
    assert resp.status_code == 200

    data = _logs(client, _admin_token(client), action=ACTION_DELETE_MEMBER)
    assert data["total"] == 1
    assert data["items"][0]["actor_type"] == "member"


def test_audit_logs_require_admin(client):
    """审计日志需 admin 令牌：未登录 401，会员令牌 401（aud 不匹配）。"""
    _admin_token(client)
    client.cookies.clear()
    assert client.get("/audit/logs").status_code == 401

    assert client.post("/member/register", json=MEMBER_PAYLOAD).status_code == 200
    login = client.post(
        "/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    member_token = login.json()["data"]["access_token"]
    resp = client.get("/audit/logs", headers={"Authorization": f"Bearer {member_token}"})
    assert resp.status_code == 401


def _seed_logs(*moments: str) -> None:
    """按指定 UTC 时刻直接落日志（created_at 取 apply default 之外的确定值）。

    业务接口写入的时间不可控，时间范围过滤只能靠定值数据验证。
    action 用 update 以便与管理端自举产生的 user.create 区分。
    """

    async def _insert(db: AsyncSession) -> None:
        for i, moment in enumerate(moments):
            db.add(
                AuditLog(
                    created_at=datetime.fromisoformat(moment),
                    actor_type="admin",
                    actor_id=1,
                    action=ACTION_UPDATE_USER,
                    target_type="user",
                    target_id=f"target-{i}",
                )
            )
        await db.commit()

    run_db(_insert)


def test_audit_logs_pagination(client):
    """审计日志分页返回 Page 结构，page_size 越界 422。"""
    token = _admin_token(client)
    data = _logs(client, token, page=1, page_size=1)
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert len(data["items"]) == 1

    resp = client.get(
        "/audit/logs", params={"page_size": 101}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422


def test_audit_write_failure_does_not_break_business(client, monkeypatch):
    """审计表不可用时只记日志，账号管理操作照常成功。"""
    from app.repositories import audit as audit_repo

    async def boom(*args, **kwargs):
        raise RuntimeError("审计表不可用（测试模拟）")

    monkeypatch.setattr(audit_repo, "create", boom)

    token = _admin_token(client)
    auth = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/user/", json={**USER_PAYLOAD, "email": "lisi@test.com", "name": "李四"}, headers=auth
    )
    assert created.status_code == 200, created.text
    assert _logs(client, token)["total"] == 0

    user_id = created.json()["data"]["id"]
    assert (
        client.patch(f"/user/{user_id}", json={"name": "李小四"}, headers=auth).status_code == 200
    )


def test_audit_logs_filter_by_time_range(client):
    """按创建时间过滤：区间外的记录被排除，结果仍按 id 倒序。"""
    token = _admin_token(client)
    _seed_logs(
        "2026-10-01T10:00:00",
        "2026-10-01T12:00:00",
        "2026-10-01T14:00:00",
    )

    data = _logs(
        client,
        token,
        action=ACTION_UPDATE_USER,
        created_at_from="2026-10-01T11:00:00Z",
        created_at_to="2026-10-01T13:00:00Z",
    )
    assert data["total"] == 1
    assert data["items"][0]["target_id"] == "target-1"


def test_audit_logs_time_range_accepts_offset(client):
    """带时区偏移的入参先换算到 UTC 再比较，闭区间端点包含。"""
    token = _admin_token(client)
    _seed_logs("2026-10-01T12:00:00")

    # 东八区 20:00 == UTC 12:00：起点正好落在记录时刻上，闭区间应命中
    data = _logs(
        client,
        token,
        action=ACTION_UPDATE_USER,
        created_at_from="2026-10-01T19:59:59+08:00",
        created_at_to="2026-10-01T20:00:00+08:00",
    )
    assert data["total"] == 1

    # 起点晚一秒（UTC 12:00:01）即落在记录之后
    data = _logs(
        client,
        token,
        action=ACTION_UPDATE_USER,
        created_at_from="2026-10-01T20:00:01+08:00",
    )
    assert data["total"] == 0


def test_audit_logs_time_range_validation(client):
    """时间参数非法一律 422：区间颠倒、格式不合法。"""
    token = _admin_token(client)
    auth = {"Authorization": f"Bearer {token}"}

    inverted = client.get(
        "/audit/logs",
        params={
            "created_at_from": "2026-10-02T00:00:00Z",
            "created_at_to": "2026-10-01T00:00:00Z",
        },
        headers=auth,
    )
    assert inverted.status_code == 422

    malformed = client.get("/audit/logs", params={"created_at_from": "昨天下午"}, headers=auth)
    assert malformed.status_code == 422
