"""登录防爆破测试：连续失败达阈值后锁定账号，成功后清零。"""

from typing import Any

import pytest

ADMIN_PAYLOAD: dict[str, Any] = {
    "name": "管理员",
    "email": "admin@test.com",
    "password": "abcd1234",
    "is_active": True,
}

MEMBER_PAYLOAD: dict[str, Any] = {
    "nickname": "小明",
    "email": "xiaoming@test.com",
    "password": "abcd1234",
}


@pytest.fixture()
def strict_guard(monkeypatch: pytest.MonkeyPatch):
    """把阈值调到 2 次，避免用例里打满默认 5 次。"""
    monkeypatch.setattr("app.login_guard.settings.LOGIN_MAX_FAILURES", 2)
    monkeypatch.setattr("app.login_guard.settings.LOGIN_LOCKOUT_SECONDS", 60)
    yield


def _wrong_login(client, email: str) -> int:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong1234"})
    return resp.status_code


def test_login_locked_after_max_failures(client, strict_guard):
    """连续失败达阈值后，即使密码正确也拒绝（429）。"""
    resp = client.post("/api/v1/auth/bootstrap", json=ADMIN_PAYLOAD)
    assert resp.status_code == 200

    assert _wrong_login(client, ADMIN_PAYLOAD["email"]) == 401
    assert _wrong_login(client, ADMIN_PAYLOAD["email"]) == 401

    locked = client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_PAYLOAD["email"], "password": ADMIN_PAYLOAD["password"]},
    )
    assert locked.status_code == 429


def test_successful_login_resets_failure_counter(client, strict_guard):
    """登录成功后清零计数：后续失败不会立刻触发锁定。"""
    client.post("/api/v1/auth/bootstrap", json=ADMIN_PAYLOAD)
    ok = client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_PAYLOAD["email"], "password": ADMIN_PAYLOAD["password"]},
    )
    assert ok.status_code == 200

    assert _wrong_login(client, ADMIN_PAYLOAD["email"]) == 401
    # 成功后再失败只累积 1 次，未达阈值 2
    assert _wrong_login(client, ADMIN_PAYLOAD["email"]) == 401
    assert _wrong_login(client, ADMIN_PAYLOAD["email"]) == 429


def test_login_lock_is_per_account(client, strict_guard):
    """锁定按账号维度：A 被锁不影响 B 登录。"""
    client.post("/api/v1/auth/bootstrap", json=ADMIN_PAYLOAD)
    other = {**ADMIN_PAYLOAD, "email": "other@test.com"}

    for _ in range(2):
        assert _wrong_login(client, ADMIN_PAYLOAD["email"]) == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": other["email"], "password": "wrong1234"},
        ).status_code
        == 401
    )


def test_member_login_shares_the_same_guard(client, strict_guard):
    """会员登录同样受防爆破保护。"""
    assert client.post("/api/v1/member/register", json=MEMBER_PAYLOAD).status_code == 200

    for _ in range(2):
        resp = client.post(
            "/api/v1/member/login",
            json={"email": MEMBER_PAYLOAD["email"], "password": "wrong1234"},
        )
        assert resp.status_code == 401

    locked = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert locked.status_code == 429
