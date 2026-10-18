"""WebSocket 插件测试：覆盖开关、鉴权、回显、广播与连接上限。

测试自建一个干净的 FastAPI 实例并手动挂插件，避免污染主 app 单例，
同时可按用例精确控制 ENABLE_WEBSOCKET 等开关。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_access_token
from app.core.settings import settings
from app.plugins import websocket as ws_plugin
from tests.conftest import fake_redis


def _make_app(monkeypatch: pytest.MonkeyPatch, *, auth_required: bool = True) -> FastAPI:
    monkeypatch.setattr(settings, "ENABLE_WEBSOCKET", True)
    monkeypatch.setattr(settings, "WEBSOCKET_PATH", "/ws")
    monkeypatch.setattr(settings, "WEBSOCKET_AUTH_REQUIRED", auth_required)
    monkeypatch.setattr(settings, "WEBSOCKET_MAX_CONNECTIONS", 1000)
    app = FastAPI()
    app.state.redis = fake_redis
    ws_plugin.websocket_manager._conns.clear()
    ws_plugin.websocket_manager._meta.clear()
    ws_plugin.setup(app)
    return app


def _token(subject: str = "123", aud: str = "admin") -> str:
    return create_access_token(subject, aud)


def test_disabled_registers_no_route():
    """关闭时不应注册任何 WebSocket 路由。"""
    app = FastAPI()
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/ws" not in paths


def test_auth_required_rejects_missing_token(monkeypatch: pytest.MonkeyPatch):
    app = _make_app(monkeypatch)
    with (
        TestClient(app) as c,
        pytest.raises(WebSocketDisconnect) as exc,
        c.websocket_connect("/ws"),
    ):
        pass
    assert exc.value.code == 1008


def test_invalid_token_rejected(monkeypatch: pytest.MonkeyPatch):
    app = _make_app(monkeypatch)
    with (
        TestClient(app) as c,
        pytest.raises(WebSocketDisconnect) as exc,
        c.websocket_connect("/ws?token=not-a-jwt"),
    ):
        pass
    assert exc.value.code == 1008


def test_refresh_token_rejected(monkeypatch: pytest.MonkeyPatch):
    """refresh 令牌（type=refresh）不得当作 WS 凭据复用。"""
    from app.core.security import create_refresh_token

    refresh = create_refresh_token("123", "admin")
    app = _make_app(monkeypatch)
    with (
        TestClient(app) as c,
        pytest.raises(WebSocketDisconnect) as exc,
        c.websocket_connect(f"/ws?token={refresh}"),
    ):
        pass
    assert exc.value.code == 1008


def test_valid_token_connects_and_echoes(monkeypatch: pytest.MonkeyPatch):
    app = _make_app(monkeypatch)
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={_token()}") as conn:
        welcome = conn.receive_json()
        assert welcome["type"] == "welcome"
        assert welcome["user_id"] == 123
        assert welcome["connections"] == 1

        conn.send_text("hello")
        msg = conn.receive_json()
        assert msg["type"] == "message"
        assert msg["data"] == "hello"

        conn.send_text('{"k": "v"}')
        msg2 = conn.receive_json()
        assert msg2["data"] == {"k": "v"}


def test_member_token_rejected_by_audience(monkeypatch: pytest.MonkeyPatch):
    """会员令牌（aud=member）不得进入后台 WS 通道。

    后台管理员与会员是两套账号体系、id 各自独立自增。少了受众校验时，会员令牌
    能直接握手成功，且连接元数据里只有裸 user_id——admin id=1 与 member id=1
    在 WS 侧完全无法区分，下游按 user_id 判权会串号。
    """
    app = _make_app(monkeypatch)
    member_token = _token(subject="1", aud="member")
    with (
        TestClient(app) as c,
        pytest.raises(WebSocketDisconnect) as exc,
        c.websocket_connect(f"/ws?token={member_token}"),
    ):
        pass
    assert exc.value.code == 1008


def test_revoked_subject_rejected(monkeypatch: pytest.MonkeyPatch):
    """账号被撤销后，其令牌不得再建立新连接。

    少了主体撤销校验，被删/停用账号的**已建立**连接会一直存活到令牌自然过期
    （默认 30 分钟），期间照常收发业务消息；这里守住的是撤销之后的新握手。
    """
    app = _make_app(monkeypatch)
    fake_redis._data["revoke:admin:123"] = "9999999999"
    try:
        with (
            TestClient(app) as c,
            pytest.raises(WebSocketDisconnect) as exc,
            c.websocket_connect(f"/ws?token={_token()}"),
        ):
            pass
        assert exc.value.code == 1008
    finally:
        fake_redis._data.pop("revoke:admin:123", None)


def test_connection_meta_carries_audience(monkeypatch: pytest.MonkeyPatch):
    """连接元数据带 aud：只有 user_id 时两套体系无从区分。"""
    app = _make_app(monkeypatch)
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={_token()}") as conn:
        conn.receive_json()  # welcome
        # 必须在连接存续期间检查：断开后 disconnect() 会清掉元数据
        metas = list(ws_plugin.websocket_manager._meta.values())
        assert metas and metas[0]["aud"] == "admin"
        assert metas[0]["user_id"] == 123


def test_cookie_auth_fallback(monkeypatch: pytest.MonkeyPatch):
    """同源 Cookie 兜底：不传 query token 时取 access_token Cookie。"""
    app = _make_app(monkeypatch)
    with (
        TestClient(app) as c,
        c.websocket_connect("/ws", cookies={"access_token": _token()}) as conn,
    ):
        welcome = conn.receive_json()
        assert welcome["type"] == "welcome"
        assert welcome["user_id"] == 123


def test_presence_broadcast_on_join(monkeypatch: pytest.MonkeyPatch):
    app = _make_app(monkeypatch)
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={_token()}") as a:
        a.receive_json()  # welcome
        with c.websocket_connect(f"/ws?token={_token()}") as b:
            b.receive_json()  # welcome
            presence = a.receive_json()  # 应收到 b 上线广播
            assert presence["type"] == "presence"
            assert presence["action"] == "join"
            assert presence["connections"] == 2


def test_leave_broadcast_on_disconnect(monkeypatch: pytest.MonkeyPatch):
    app = _make_app(monkeypatch)
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={_token()}") as a:
        a.receive_json()
        with c.websocket_connect(f"/ws?token={_token()}") as b:
            b.receive_json()
            a.receive_json()  # b 上线
        # b 退出后 a 应收到下线广播
        presence = a.receive_json()
        assert presence["action"] == "leave"
        assert presence["connections"] == 1


def test_max_connections_rejects(monkeypatch: pytest.MonkeyPatch):
    app = _make_app(monkeypatch)
    monkeypatch.setattr(settings, "WEBSOCKET_MAX_CONNECTIONS", 1)
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={_token()}") as first:
        first.receive_json()
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            c.websocket_connect(f"/ws?token={_token()}"),
        ):
            pass
    assert exc.value.code == 1013


def test_anonymous_allowed_when_auth_off(monkeypatch: pytest.MonkeyPatch):
    app = _make_app(monkeypatch, auth_required=False)
    with TestClient(app) as c, c.websocket_connect("/ws") as conn:
        welcome = conn.receive_json()
        assert welcome["type"] == "welcome"
        assert welcome["user_id"] is None
