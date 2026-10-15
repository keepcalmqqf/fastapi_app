"""邮箱验证测试：注册投递、令牌验证、类型/audience 隔离、强制验证开关。"""

from urllib.parse import parse_qs, urlparse

import pytest

from app.core.security import create_access_token, create_email_verify_token
from app.core.settings import settings
from app.services import mailer

MEMBER_PAYLOAD = {
    "nickname": "小明",
    "email": "xiaoming@test.com",
    "password": "abcd1234",
}

USER_PAYLOAD = {
    "name": "张三",
    "email": "zhangsan@test.com",
    "password": "abcd1234",
    "is_active": True,
}


def _capture_mail(monkeypatch) -> list[tuple[str, str]]:
    """拦截验证邮件，返回 (email, link) 列表。"""
    sent: list[tuple[str, str]] = []

    async def fake_send(email: str, link: str) -> None:
        sent.append((email, link))

    monkeypatch.setattr(mailer, "send_verification_email", fake_send)
    return sent


def _token_from_link(link: str) -> str:
    return parse_qs(urlparse(link).query)["token"][0]


def test_register_sends_verification_email(client, monkeypatch):
    """会员注册成功后投递验证邮件（默认实现只记日志，这里拦截验证投递行为）。"""
    sent = _capture_mail(monkeypatch)
    assert client.post("/api/v1/member/register", json=MEMBER_PAYLOAD).status_code == 200
    assert len(sent) == 1
    assert sent[0][0] == MEMBER_PAYLOAD["email"]


def test_verify_email_flow(client, monkeypatch):
    """完整链路：注册 → 取链接中的 token → 验证 → email_verified 置真。"""
    sent = _capture_mail(monkeypatch)
    registered = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    assert registered.status_code == 200
    member_id = registered.json()["data"]["id"]
    assert registered.json()["data"]["email_verified"] is False

    token = _token_from_link(sent[0][1])
    resp = client.post("/api/v1/member/verify-email", json={"token": token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["email_verified"] is True

    # 用会员令牌确认状态已落库
    login = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert login.status_code == 200
    me = client.get(
        "/api/v1/member/me",
        headers={"Authorization": f"Bearer {login.json()['data']['access_token']}"},
    )
    assert me.json()["data"]["id"] == member_id
    assert me.json()["data"]["email_verified"] is True


def test_verify_email_rejects_access_token(client):
    """访问令牌不能当验证令牌用（type 不匹配）。"""
    resp = client.post(
        "/api/v1/member/verify-email", json={"token": create_access_token("1", "member")}
    )
    assert resp.status_code == 401


def test_verify_email_rejects_cross_audience(client, monkeypatch):
    """会员的验证令牌不能用于验证后台账号（aud 不匹配）。"""
    client.post("/api/v1/user/", json=USER_PAYLOAD)
    token = create_email_verify_token("1", "member")
    resp = client.post("/api/v1/user/verify-email", json={"token": token})
    assert resp.status_code == 401


def test_verify_email_rejects_garbage_token(client):
    resp = client.post("/api/v1/member/verify-email", json={"token": "not-a-token"})
    assert resp.status_code == 401


def test_verify_email_unknown_subject(client):
    """令牌签名有效但账号不存在：404。"""
    resp = client.post(
        "/api/v1/member/verify-email", json={"token": create_email_verify_token("9999", "member")}
    )
    assert resp.status_code == 404


def test_resend_requires_login(client, monkeypatch):
    """重发验证邮件需要登录。"""
    _capture_mail(monkeypatch)
    client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    client.cookies.clear()
    assert client.post("/api/v1/member/resend-verification").status_code == 401


def test_resend_after_verified_is_noop(client, monkeypatch):
    """已验证的账号重发时直接返回，不再投递。"""
    sent = _capture_mail(monkeypatch)
    client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    sent.clear()

    login = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    token = login.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    resp = client.post("/api/v1/member/resend-verification", headers=auth)
    assert resp.status_code == 200
    assert len(sent) == 1  # 首次重发会投递

    verify_resp = client.post(
        "/api/v1/member/verify-email", json={"token": _token_from_link(sent[0][1])}
    )
    assert verify_resp.status_code == 200
    sent.clear()

    again = client.post("/api/v1/member/resend-verification", headers=auth)
    assert again.status_code == 200
    assert len(sent) == 0
    assert "已验证" in again.json()["message"]


def test_login_allowed_when_verification_not_required(client):
    """默认不强制验证：未验证也能登录（向后兼容）。"""
    client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    resp = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert resp.status_code == 200


def test_login_blocked_when_verification_required(client, monkeypatch):
    """开启 REQUIRE_EMAIL_VERIFICATION 后，未验证账号登录返回 403。"""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", True)
    client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    resp = client.post(
        "/api/v1/member/login",
        json={"email": MEMBER_PAYLOAD["email"], "password": MEMBER_PAYLOAD["password"]},
    )
    assert resp.status_code == 403


def test_mail_failure_does_not_break_register(client, monkeypatch):
    """邮件服务不可用时注册照常成功。"""

    async def boom(email: str, link: str) -> None:
        raise RuntimeError("邮件服务不可用（测试模拟）")

    monkeypatch.setattr(mailer, "send_verification_email", boom)
    resp = client.post("/api/v1/member/register", json=MEMBER_PAYLOAD)
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/member/verify-email", "/api/v1/user/verify-email"])
def test_verify_email_requires_token_body(client, path):
    """缺少 token 字段返回统一格式 422。"""
    resp = client.post(path, json={})
    assert resp.status_code == 422
    assert resp.json()["code"] == 422
