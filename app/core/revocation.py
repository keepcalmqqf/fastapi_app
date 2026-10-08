"""主体级令牌撤销状态（Redis）。

access / refresh 的 jti 无法由主体反查——同一账号可多设备并存多个 jti，
因此「终止该账号全部会话」时无法逐个删除 `refresh:{jti}`。这里改为记录
「该主体被撤销的时间点」，凡是签发时间不晚于它的令牌一律失效：一次写入
即可作废该主体已签发的全部令牌，且账号重新启用后旧会话不会复活
（重新登录签发的令牌 iat 更大，不受影响）。

适用范围：管理员软删用户、将用户 `is_active` 置为 False 等账号级操作。
登出**不应**调用本模块——登出只让当前设备的令牌失效，不能影响该账号
在其他设备上的登录，那由 `app/services/tokens.py::revoke_tokens` 逐 jti 处理。
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.settings import settings

logger = logging.getLogger("fastapi_app")

# 撤销标记的存活时间：只需覆盖现存令牌的最长有效期（即 refresh 有效期），
# 到期后现存令牌已自然过期，标记再留着没有意义。
_REVOKE_TTL_SECONDS = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400


def revocation_key(aud: str, subject: str) -> str:
    """主体级撤销标记的 Redis key。"""
    return f"revoke:{aud}:{subject}"


async def revoke_subject(aud: str, subject: str, redis: Any) -> None:
    """作废某主体（aud + subject）已签发的全部令牌（access + refresh）。

    失败只记日志不抛出：撤销是可用性优先的旁路校验，与黑名单 fail-open 保持一致。
    账号被删除或禁用本身已由 `get_current_user`/`get_current_member` 的库内校验
    （不存在或 `is_active=False` 一律 401）兜底，因此此处失败不会直接放开访问，
    仅可能在 Redis 故障期间让「重新启用账号后旧会话复活」这一窄路径未被覆盖。
    """
    if redis is None:
        return
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        await redis.set(revocation_key(aud, subject), str(now), ex=_REVOKE_TTL_SECONDS)
    except Exception:
        logger.warning("写入令牌撤销标记失败: aud=%s subject=%s", aud, subject, exc_info=True)


async def is_subject_revoked(aud: str, subject: str, issued_at: Any, redis: Any) -> bool:
    """令牌是否已被主体级撤销（判定规则见模块 docstring）。

    `iat` 为秒级精度，故用 `<=` 比较：宁可把撤销后同一秒内签发的令牌一并作废
    （用户 1 秒后重试即可恢复），也不能漏放撤销前签发的令牌。
    Redis 异常时 fail-open 放行并记日志，与黑名单校验保持一致。
    """
    if redis is None or not subject or issued_at is None:
        return False
    try:
        marker = await redis.get(revocation_key(aud, subject))
    except Exception:
        logger.warning("读取令牌撤销标记失败，本次放行（fail-open）", exc_info=True)
        return False
    if marker is None:
        return False
    try:
        return int(issued_at) <= int(marker)
    except (TypeError, ValueError):
        return False
