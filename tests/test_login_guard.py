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


def _right_login(client, email: str, password: str) -> int:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
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
    """锁定按账号维度：A 被锁不影响 B 登录。

    此前这条用例是假绿灯：B 账号从未创建，且用的是错误密码，无论锁定逻辑是
    「按账号」「全局」还是整个删掉，它都必然返回 401。真正的断言必须是
    **B 用正确密码能登录成功**——只有这样才能区分「按账号锁定」与「全局锁定」。
    """
    resp = client.post("/api/v1/auth/bootstrap", json=ADMIN_PAYLOAD)
    assert resp.status_code == 200
    other = {**ADMIN_PAYLOAD, "email": "other@test.com"}
    # B 必须是真实存在的账号，否则「登录成功」这条断言无从谈起
    login = client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_PAYLOAD["email"], "password": ADMIN_PAYLOAD["password"]},
    )
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]
    created = client.post("/api/v1/user/", json=other, headers={"Authorization": f"Bearer {token}"})
    assert created.status_code == 200, created.text
    # 先确认 B 在未被牵连时确实能登录（排除账号本身有问题导致的假通过）
    assert _right_login(client, other["email"], other["password"]) == 200

    # A 连续失败触顶：第 2 次失败时才写入锁标记，故它自身仍返回 401，
    # 之后（即便密码正确）才被锁拦下
    assert _wrong_login(client, ADMIN_PAYLOAD["email"]) == 401
    assert _wrong_login(client, ADMIN_PAYLOAD["email"]) == 401
    assert _right_login(client, ADMIN_PAYLOAD["email"], ADMIN_PAYLOAD["password"]) == 429

    # 关键断言：A 被锁期间 B 仍能正常登录。全局锁定的话这里会是 429。
    assert _right_login(client, other["email"], other["password"]) == 200


def test_failure_counter_and_ttl_are_written_atomically(client, strict_guard, monkeypatch):
    """INCR 与 EXPIRE 必须同批到达，不允许出现「有计数无 TTL」的中间态。

    两步下发时，进程在两者之间被杀会让计数键永不过期：失败次数无限累加，账号
    在每次锁定期结束后立刻重新被锁，用户无法自行恢复。这条用例盯的是下发方式
    （走 pipeline），而不只是最终 TTL 值——只看 TTL 的话，两步下发在正常运行下
    也一样是绿的。
    """
    from tests.conftest import fake_redis

    # 显式固定 IP 维度阈值：`LOGIN_IP_MAX_FAILURES` 只有在它 > 0 时才会计数，
    # 而 .env 里配的是 0（与代码默认值 20 相反）。不 pin 住的话，「有几个计数键」
    # 会随环境变化，用例在 CI 与本地表现不一致——正是本文件其它用例靠 strict_guard
    # 规避的那一类问题。
    monkeypatch.setattr("app.login_guard.settings.LOGIN_IP_MAX_FAILURES", 20)

    client.post("/api/v1/auth/bootstrap", json=ADMIN_PAYLOAD)

    batches: list[list[str]] = []
    fake = fake_redis
    real_pipeline = fake.pipeline

    def _spy_pipeline():
        pipe = real_pipeline()
        real_execute = pipe.execute

        async def _execute():
            batches.append([name for name, _args, _kwargs in pipe._ops])
            return await real_execute()

        pipe.execute = _execute  # type: ignore[method-assign]
        return pipe

    monkeypatch.setattr(fake, "pipeline", _spy_pipeline)

    _wrong_login(client, ADMIN_PAYLOAD["email"])

    incr_batches = [b for b in batches if "incr" in b]
    assert incr_batches, "失败计数没有走 pipeline，原子性无从保证"
    # 计数自增与 TTL 下发必须在同一批，否则中间态窗口依然存在
    assert all("expire" in b for b in incr_batches)
    counters = {k: v for k, v in fake._ttl.items() if k.startswith("loginfail:")}
    # 账号与 IP 两个维度各一个计数键，都必须带上 TTL
    assert counters and set(counters.values()) == {60}, counters


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
