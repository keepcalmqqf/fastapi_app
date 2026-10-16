from typing import Any

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
    resp = client.post("/api/v1/user/", json=payload)
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
