from typing import Any

from tests.conftest import run_db

ADMIN_PAYLOAD: dict[str, Any] = {
    "name": "管理员",
    "email": "admin@test.com",
    "password": "abcd1234",
    "is_active": True,
}


def _login(client, email: str, password: str = "abcd1234") -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["data"]["access_token"]


def _create_first_admin(client, payload: dict | None = None) -> str:
    """空库自举创建首个管理员（自动授予 super_admin）并登录，返回 token。"""
    payload = payload or ADMIN_PAYLOAD
    resp = client.post("/api/v1/auth/bootstrap", json=payload)
    assert resp.status_code == 200, f"创建首个管理员失败: {resp.text}"
    return _login(client, payload["email"], payload["password"])


def _create_user(client, token: str, email: str, password: str = "abcd1234") -> dict:
    resp = client.post(
        "/api/v1/user/",
        json={"name": "普通用户", "email": email, "password": password, "is_active": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_bootstrap_user_is_super_admin(client):
    """自举首个管理员应自动获得 super_admin 角色，且角色含全部权限。"""
    token = _create_first_admin(client)
    me = client.get("/api/v1/user/me", headers=_auth(token))
    uid = me.json()["data"]["id"]

    roles = client.get(f"/api/v1/user/{uid}/roles", headers=_auth(token))
    assert roles.status_code == 200
    data = roles.json()["data"]
    assert any(r["code"] == "super_admin" for r in data)
    super_admin = next(r for r in data if r["code"] == "super_admin")
    codes = {p["code"] for p in super_admin["permissions"]}
    assert {"user:read", "user:write", "member:read", "role:write"} <= codes


def test_user_without_role_forbidden(client):
    """无角色的用户访问受保护接口返回 403。"""
    token = _create_first_admin(client)
    _create_user(client, token, "plain@test.com")
    plain_token = _login(client, "plain@test.com")

    # 无 user:read → 403
    resp = client.get("/api/v1/user/", headers=_auth(plain_token))
    assert resp.status_code == 403
    # 无 member:read → 403
    resp = client.get("/api/v1/member/", headers=_auth(plain_token))
    assert resp.status_code == 403
    # 无 role:read → 403
    resp = client.get("/api/v1/role/", headers=_auth(plain_token))
    assert resp.status_code == 403


def test_assign_viewer_role_grants_read_only(client):
    """授予 viewer（仅 user/member 读）后，可读取但写入仍被拒。"""
    token = _create_first_admin(client)
    plain = _create_user(client, token, "plain@test.com")
    plain_token = _login(client, "plain@test.com")

    # 赋角色前只读也被拒
    assert client.get("/api/v1/user/", headers=_auth(plain_token)).status_code == 403

    assign = client.post(
        f"/api/v1/user/{plain['id']}/roles",
        json={"role_codes": ["viewer"]},
        headers=_auth(token),
    )
    assert assign.status_code == 200
    assert {r["code"] for r in assign.json()["data"]} == {"viewer"}

    # 赋角色后可读
    assert client.get("/api/v1/user/", headers=_auth(plain_token)).status_code == 200
    # 但仍无 user:write → 创建用户被拒
    create = client.post(
        "/api/v1/user/",
        json={"name": "xyz", "email": "x@test.com", "password": "abcd1234"},
        headers=_auth(plain_token),
    )
    assert create.status_code == 403


def test_assign_admin_role_grants_write(client):
    """授予 admin 角色后获得 user/member 读写，但仍无 role:write。"""
    token = _create_first_admin(client)
    plain = _create_user(client, token, "plain@test.com")
    plain_token = _login(client, "plain@test.com")

    client.post(
        f"/api/v1/user/{plain['id']}/roles",
        json={"role_codes": ["admin"]},
        headers=_auth(token),
    )
    assert (
        client.post(
            "/api/v1/user/",
            json={"name": "yyy", "email": "y@test.com", "password": "abcd1234"},
            headers=_auth(plain_token),
        ).status_code
        == 200
    )
    # admin 无 role:write → 创建角色被拒
    assert (
        client.post(
            "/api/v1/role/", json={"name": "z", "code": "zrole"}, headers=_auth(plain_token)
        ).status_code
        == 403
    )


def test_role_and_permission_management(client):
    """super_admin 可列角色/权限、创建角色、配置权限、删除自定义角色。"""
    token = _create_first_admin(client)
    auth = _auth(token)

    roles = client.get("/api/v1/role/", headers=auth)
    assert roles.status_code == 200
    role_data = roles.json()["data"]
    codes = {r["code"] for r in role_data}
    assert {"super_admin", "admin", "viewer"} <= codes

    perms = client.get("/api/v1/permission/", headers=auth)
    assert perms.status_code == 200
    assert any(p["code"] == "user:read" for p in perms.json()["data"])

    created = client.post("/api/v1/role/", json={"name": "自定义", "code": "custom"}, headers=auth)
    assert created.status_code == 200
    rid = created.json()["data"]["id"]

    set_perm = client.post(
        f"/api/v1/role/{rid}/permissions",
        json={"permission_codes": ["user:read", "member:read"]},
        headers=auth,
    )
    assert set_perm.status_code == 200
    assert {p["code"] for p in set_perm.json()["data"]["permissions"]} == {
        "user:read",
        "member:read",
    }

    # 系统角色不可删除
    viewer_id = next(r["id"] for r in role_data if r["code"] == "viewer")
    assert client.delete(f"/api/v1/role/{viewer_id}", headers=auth).status_code == 400

    # 自定义角色可删除
    assert client.delete(f"/api/v1/role/{rid}", headers=auth).status_code == 200
    assert client.get(f"/api/v1/role/{rid}", headers=auth).status_code == 404


def test_create_role_duplicate_code(client):
    token = _create_first_admin(client)
    first = client.post("/api/v1/role/", json={"name": "重复", "code": "dup"}, headers=_auth(token))
    assert first.status_code == 200
    second = client.post(
        "/api/v1/role/", json={"name": "重复2", "code": "dup"}, headers=_auth(token)
    )
    assert second.status_code == 409


def test_create_permission_duplicate_code(client):
    token = _create_first_admin(client)
    first = client.post(
        "/api/v1/permission/",
        json={"name": "自定义权限", "code": "custom:perm"},
        headers=_auth(token),
    )
    assert first.status_code == 200
    second = client.post(
        "/api/v1/permission/",
        json={"name": "自定义权限2", "code": "custom:perm"},
        headers=_auth(token),
    )
    assert second.status_code == 409


def test_unknown_role_code_ignored_on_assign(client):
    """分配角色时传入未知 code 被忽略，不报错。"""
    token = _create_first_admin(client)
    plain = _create_user(client, token, "plain@test.com")

    assign = client.post(
        f"/api/v1/user/{plain['id']}/roles",
        json={"role_codes": ["viewer", "not_exist"]},
        headers=_auth(token),
    )
    assert assign.status_code == 200
    assert {r["code"] for r in assign.json()["data"]} == {"viewer"}


def test_delete_role_revokes_user_permissions(client):
    """自定义角色被删除后，原持有该角色的用户权限应被即时回收。

    回归测试：此前 get_user_permission_codes 未过滤 Role.deleted_at，
    软删角色后权限仍残留，导致界面隐藏角色但接口仍放行。
    """
    token = _create_first_admin(client)
    auth = _auth(token)
    plain = _create_user(client, token, "plain@test.com")
    plain_token = _login(client, "plain@test.com")

    # 建一个仅含 user:write 的自定义角色并赋给用户
    created = client.post("/api/v1/role/", json={"name": "可写", "code": "writer"}, headers=auth)
    assert created.status_code == 200
    rid = created.json()["data"]["id"]
    perm = client.post(
        f"/api/v1/role/{rid}/permissions",
        json={"permission_codes": ["user:write"]},
        headers=auth,
    )
    assert perm.status_code == 200
    assign = client.post(
        f"/api/v1/user/{plain['id']}/roles",
        json={"role_codes": ["writer"]},
        headers=auth,
    )
    assert assign.status_code == 200

    def _create_plain() -> int:
        return client.post(
            "/api/v1/user/",
            json={"name": "zzz", "email": "z@test.com", "password": "abcd1234"},
            headers=_auth(plain_token),
        ).status_code

    # 赋权后可创建用户
    assert _create_plain() == 200

    # 删除该角色（软删除），权限应即时回收
    assert client.delete(f"/api/v1/role/{rid}", headers=auth).status_code == 200

    # 角色删除后不再持有 user:write：创建用户被拒，且角色列表中已无该角色
    assert _create_plain() == 403
    roles = client.get(f"/api/v1/user/{plain['id']}/roles", headers=auth)
    assert not any(r["code"] == "writer" for r in roles.json()["data"])


def test_admin_cannot_assign_super_admin(client):
    """普通管理员（拥有 user:write 但非 super_admin）不能分配 super_admin 角色。

    回归测试：此前 set_user_roles 对 super_admin code 无校验，任何 admin 可自我提权。
    """
    token = _create_first_admin(client)
    auth = _auth(token)
    mgr = _create_user(client, token, "mgr@test.com")
    # 该用户授予 admin 角色（含 user:write，可调用角色分配接口）
    client.post(
        f"/api/v1/user/{mgr['id']}/roles",
        json={"role_codes": ["admin"]},
        headers=auth,
    )
    mgr_token = _login(client, "mgr@test.com")

    # 非 super_admin 尝试把 super_admin 分配给他人 → 403，且未被提权
    resp = client.post(
        f"/api/v1/user/{mgr['id']}/roles",
        json={"role_codes": ["super_admin"]},
        headers=_auth(mgr_token),
    )
    assert resp.status_code == 403
    roles = client.get(f"/api/v1/user/{mgr['id']}/roles", headers=auth)
    assert not any(r["code"] == "super_admin" for r in roles.json()["data"])


def test_super_admin_can_assign_super_admin(client):
    """超级管理员可分配 super_admin 角色，确认拦截未误伤正常提权场景。"""
    token = _create_first_admin(client)
    auth = _auth(token)
    other = _create_user(client, token, "other@test.com")
    resp = client.post(
        f"/api/v1/user/{other['id']}/roles",
        json={"role_codes": ["super_admin"]},
        headers=auth,
    )
    assert resp.status_code == 200
    assert any(r["code"] == "super_admin" for r in resp.json()["data"])


def test_audit_read_granted_to_super_admin_and_admin(client):
    """审计日志需 audit:read：super_admin 与 admin 可读。"""
    token = _create_first_admin(client)
    assert client.get("/api/v1/audit/logs", headers=_auth(token)).status_code == 200

    mgr = _create_user(client, token, "mgr@test.com")
    client.post(
        f"/api/v1/user/{mgr['id']}/roles",
        json={"role_codes": ["admin"]},
        headers=_auth(token),
    )
    mgr_token = _login(client, "mgr@test.com")
    assert client.get("/api/v1/audit/logs", headers=_auth(mgr_token)).status_code == 200


def test_audit_read_denied_for_viewer(client):
    """访客角色（只读业务数据）不应读到审计日志。

    回归：此前 /audit/logs 只校验登录态，任意后台账号都能拉取含 IP 与变更详情的全量审计。
    """
    token = _create_first_admin(client)
    viewer = _create_user(client, token, "viewer@test.com")
    client.post(
        f"/api/v1/user/{viewer['id']}/roles",
        json={"role_codes": ["viewer"]},
        headers=_auth(token),
    )
    viewer_token = _login(client, "viewer@test.com")
    resp = client.get("/api/v1/audit/logs", headers=_auth(viewer_token))
    assert resp.status_code == 403


def test_audit_permission_is_in_seed_data(client):
    """种子权限必须包含 audit:read，否则前端菜单与后端守卫口径会再次错位。"""
    token = _create_first_admin(client)
    perms = client.get("/api/v1/permission/", headers=_auth(token))
    assert perms.status_code == 200
    assert any(p["code"] == "audit:read" for p in perms.json()["data"])


def test_seed_updates_backfill_missing_permission(client):
    """存量库补种：删掉 audit:read 后可由 ensure_seed_updates 幂等补齐。

    回归：ensure_seeded 在角色已存在时直接返回，只改种子代码对存量库无效。
    """
    from sqlalchemy import delete
    from sqlalchemy import select as sa_select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Permission, role_permission_table
    from app.services import rbac as rbac_service

    token = _create_first_admin(client)

    async def _drop(db: AsyncSession) -> None:
        perm = await db.scalar(sa_select(Permission).where(Permission.code == "audit:read"))
        assert perm is not None
        await db.execute(
            delete(role_permission_table).where(
                role_permission_table.c.permission_id == int(perm.id)
            )
        )
        await db.delete(perm)
        await db.commit()

    run_db(_drop)

    async def _backfill(db: AsyncSession) -> bool:
        return await rbac_service.ensure_seed_updates(db)

    assert run_db(_backfill) is True
    # 再次执行无变更，确认幂等
    assert run_db(_backfill) is False

    # 补齐后超管重新获得 audit:read
    me = client.get("/api/v1/user/me", headers=_auth(token))
    uid = me.json()["data"]["id"]
    roles = client.get(f"/api/v1/user/{uid}/roles", headers=_auth(token))
    codes = {p["code"] for r in roles.json()["data"] for p in r.get("permissions", [])}
    assert "audit:read" in codes
