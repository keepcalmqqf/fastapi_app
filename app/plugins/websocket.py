"""WebSocket 实时通信插件：可插拔的双向通道，开关由配置控制。

设计取舍：
- 开关 ENABLE_WEBSOCKET 关闭时 `setup` 不注册任何路由，行为与未集成完全一致。
- 鉴权复用现有 JWT：浏览器对 WebSocket 握手无法自定义请求头，故令牌取自
  query 参数 `token`（跨域/小程序友好）或同源自动携带的 `access_token` Cookie
  （与 REST 端点取令牌顺序一致）。验签失败 / 过期 / 非访问令牌一律拒绝握手
  （1008 策略违规）。
- 鉴权只做「签名 + 过期 + 类型 + 登出黑名单」四道轻量校验，不查库判断账号状态，
  避免每次握手都打 DB。需要账号级实时状态（禁用/删除后立即断连）的业务，
  可在自己的推送逻辑里用 `websocket_manager.meta(conn_id)["user_id"]` 重新核验。
- 连接由 ConnectionManager 集中管理并暴露为模块级单例 `websocket_manager`，
  业务层可直接 `from app.plugins.websocket import websocket_manager` 后调用
  `broadcast(...)` 主动推送，无需改动本插件。

消息协议（JSON）：
- 握手成功：{"type": "welcome", "conn_id", "user_id", "connections"}
- 他人上下线：{"type": "presence", "action": "join"|"leave", "connections"}
- 收到文本：{"type": "message", "from", "user_id", "data"}（data 为解析后的 JSON 或原文）
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, WebSocketException

from app.core.security import decode_access_token
from app.core.settings import settings

logger = logging.getLogger("fastapi_app")

# WebSocket 关闭码（与 starlette.status 的 HTTP 码不同，WS 用独立的值域）
_WS_NORMAL = 1000
_WS_POLICY_VIOLATION = 1008  # 鉴权失败 / 令牌无效
_WS_TRY_AGAIN_LATER = 1013  # 连接数达上限
_WS_INTERNAL_ERROR = 1011  # 服务端内部异常


class ConnectionManager:
    """内存连接注册表：按连接 ID 管理 WebSocket 与元数据，支持定向/广播推送。

    高并发下 `dict` 的增删查在 CPython 下为原子操作，常规规模足够；
    若后续要支撑海量连接，再引入分片或 Redis 发布订阅做跨进程广播。
    """

    def __init__(self) -> None:
        self._conns: dict[str, WebSocket] = {}
        self._meta: dict[str, dict[str, Any]] = {}

    def count(self) -> int:
        """当前活跃连接数。"""
        return len(self._conns)

    def meta(self, conn_id: str) -> dict[str, Any]:
        """某连接的元数据（如 user_id）；不存在时返回空 dict。"""
        return self._meta.get(conn_id, {})

    async def connect(
        self, conn_id: str, ws: WebSocket, meta: dict[str, Any] | None = None
    ) -> None:
        self._conns[conn_id] = ws
        self._meta[conn_id] = meta or {}

    def disconnect(self, conn_id: str) -> None:
        self._conns.pop(conn_id, None)
        self._meta.pop(conn_id, None)

    async def send(self, conn_id: str, message: dict[str, Any]) -> bool:
        """向单个连接推送；连接已断开则清理并返回 False。"""
        ws = self._conns.get(conn_id)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:  # noqa: BLE001 - 对端异常断开，回收连接即可
            self.disconnect(conn_id)
            return False

    async def broadcast(self, message: dict[str, Any], exclude: str | None = None) -> int:
        """向全部连接广播（可排除某一连接）；返回成功送达的条数。

        发送过程对端掉线会被逐个捕获并立即回收，不影响其余连接。
        """
        sent = 0
        dead: list[str] = []
        for conn_id, ws in list(self._conns.items()):
            if conn_id == exclude:
                continue
            try:
                await ws.send_json(message)
                sent += 1
            except Exception:  # noqa: BLE001
                dead.append(conn_id)
        for conn_id in dead:
            self.disconnect(conn_id)
        return sent


# 模块级单例：业务层经此主动推送，测试可重置其内部状态
websocket_manager = ConnectionManager()


async def authenticate(websocket: WebSocket, token: str | None) -> int | None:
    """校验 WebSocket 连接的 JWT，返回 subject（用户 ID）或 None（匿名）。

    取令牌顺序与 REST 端点一致：query 参数 `token` 优先，其次 `access_token` Cookie。
    不要求鉴权（WEBSOCKET_AUTH_REQUIRED=false）时直接返回 None，允许匿名连接。
    """
    if not settings.WEBSOCKET_AUTH_REQUIRED:
        return None
    if not token:
        token = websocket.cookies.get("access_token")
    if not token:
        raise WebSocketException(code=_WS_POLICY_VIOLATION, reason="未提供认证令牌")

    payload = decode_access_token(token)
    if payload is None:
        raise WebSocketException(code=_WS_POLICY_VIOLATION, reason="认证令牌无效或已过期")
    # 只接受访问令牌：拒绝把 refresh 令牌（有效期更长）当作 WS 凭据复用
    token_type = payload.get("type")
    if token_type not in (None, "access"):
        raise WebSocketException(code=_WS_POLICY_VIOLATION, reason="认证令牌无效或已过期")
    subject = payload.get("sub")
    if not subject or not str(subject).isdigit():
        raise WebSocketException(code=_WS_POLICY_VIOLATION, reason="认证令牌无效或已过期")

    # 登出黑名单校验：Redis 不可用时放行（fail-open），与 REST 端点行为一致
    redis = getattr(websocket.app.state, "redis", None)
    jti = payload.get("jti")
    if jti and redis is not None:
        try:
            if await redis.get(f"blacklist:{jti}") is not None:
                raise WebSocketException(code=_WS_POLICY_VIOLATION, reason="认证令牌已失效")
        except WebSocketException:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("WebSocket 黑名单查询失败，本次放行（fail-open）", exc_info=True)
    return int(subject)


async def _handle_connection(websocket: WebSocket, token: str | None = None) -> None:
    """单连接的完整生命周期：鉴权 → 上限检查 → 收发循环 → 清理。"""
    subject = await authenticate(websocket, token)

    # 上限检查必须在 accept 之前，否则拒绝时无法回带关闭码
    if (
        settings.WEBSOCKET_MAX_CONNECTIONS
        and websocket_manager.count() >= settings.WEBSOCKET_MAX_CONNECTIONS
    ):
        raise WebSocketException(code=_WS_TRY_AGAIN_LATER, reason="连接数已达上限")

    conn_id = uuid.uuid4().hex
    await websocket.accept()
    await websocket_manager.connect(conn_id, websocket, {"user_id": subject})

    try:
        # 通知本人连接成功，并广播上线事件给其余连接
        await websocket_manager.send(
            conn_id,
            {
                "type": "welcome",
                "conn_id": conn_id,
                "user_id": subject,
                "connections": websocket_manager.count(),
            },
        )
        await websocket_manager.broadcast(
            {"type": "presence", "action": "join", "connections": websocket_manager.count()},
            exclude=conn_id,
        )

        while True:
            raw = await websocket.receive_text()
            try:
                payload: Any = json.loads(raw)
            except (ValueError, TypeError):
                payload = raw
            await websocket_manager.send(
                conn_id,
                {"type": "message", "from": conn_id, "user_id": subject, "data": payload},
            )
    except WebSocketDisconnect:
        websocket_manager.disconnect(conn_id)
        await websocket_manager.broadcast(
            {"type": "presence", "action": "leave", "connections": websocket_manager.count()}
        )
    except WebSocketException:
        # 主动/异常关闭：清理后重抛，由框架结束握手
        websocket_manager.disconnect(conn_id)
        raise
    except Exception:  # noqa: BLE001
        logger.exception("WebSocket 连接异常")
        websocket_manager.disconnect(conn_id)
        with contextlib.suppress(Exception):
            await websocket.close(code=_WS_INTERNAL_ERROR)


def setup(app: FastAPI) -> None:
    """挂载 WebSocket 端点（路径取自配置）。"""
    app.websocket(settings.WEBSOCKET_PATH)(_handle_connection)
    # 暴露单例到应用状态，便于排障时统一查看连接数
    app.state.websocket_manager = websocket_manager
    logger.info(
        "插件已启用: websocket (路径 %s，鉴权 %s，上限 %d)",
        settings.WEBSOCKET_PATH,
        "开启" if settings.WEBSOCKET_AUTH_REQUIRED else "关闭",
        settings.WEBSOCKET_MAX_CONNECTIONS,
    )
