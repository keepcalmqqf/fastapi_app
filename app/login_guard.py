"""登录防爆破：按账号与来源 IP 统计连续失败次数，超限后临时锁定。

设计取舍：
- 限流插件按 IP 全局计数，无法识别「针对单个账号的慢速撞库」，这里按账号维度补一层。
- 计数只存 Redis，Redis 不可用时 fail-open（放行并告警），避免缓存故障把正常用户锁在门外；
  这是与令牌黑名单/主体撤销一致的可用性优先取舍。
- 锁定期间即使密码正确也拒绝，避免爆破与撞库的同时防止账号被持续探测。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException

from app.core.settings import settings

logger = logging.getLogger("fastapi_app")

# 失败计数 / 锁定标记的前缀
_FAIL_PREFIX = "loginfail"
_LOCK_PREFIX = "loginlock"


def _fail_key(subject: str) -> str:
    return f"{_FAIL_PREFIX}:{subject}"


def _lock_key(subject: str) -> str:
    return f"{_LOCK_PREFIX}:{subject}"


class LoginGuard:
    """登录失败计数与锁定；subject 由调用方按「主体类型:标识」传入（如 admin:1）。"""

    @property
    def max_failures(self) -> int:
        return settings.LOGIN_MAX_FAILURES

    @property
    def lockout_seconds(self) -> int:
        return settings.LOGIN_LOCKOUT_SECONDS

    @property
    def ip_max_failures(self) -> int:
        """来源 IP 维度阈值；0 表示不启用（IP 层已有全局限流兜底）。"""
        return settings.LOGIN_IP_MAX_FAILURES

    async def _locked(self, redis: Any, key: str) -> bool:
        if redis is None:
            return False
        try:
            return await redis.exists(key) > 0
        except Exception:
            logger.warning("读取登录锁定标记失败，按未锁定处理", exc_info=True)
            return False

    async def is_locked(self, redis: Any, subject: str, ip: str | None = None) -> bool:
        """账号或来源 IP 任一被锁定即返回 True。"""
        if self.max_failures <= 0:
            return False
        if await self._locked(redis, _lock_key(subject)):
            return True
        if self.ip_max_failures > 0 and ip:
            return await self._locked(redis, _lock_key(f"ip:{ip}"))
        return False

    async def ensure_not_locked(self, redis: Any, subject: str, ip: str | None = None) -> None:
        """登录前调用：被锁定则抛 429，附带剩余锁定时间。"""
        if not await self.is_locked(redis, subject, ip):
            return
        seconds = await self.remaining_seconds(redis, subject, ip)
        raise _locked_error(seconds)

    async def remaining_seconds(self, redis: Any, subject: str, ip: str | None = None) -> int:
        """锁定剩余秒数；取不到时回退为配置的锁定时长。"""
        keys = [_lock_key(subject)]
        if self.ip_max_failures > 0 and ip:
            keys.append(_lock_key(f"ip:{ip}"))
        if redis is None:
            return self.lockout_seconds
        for key in keys:
            try:
                ttl = await redis.ttl(key)
            except Exception:
                continue
            if isinstance(ttl, int) and ttl > 0:
                return ttl
        return self.lockout_seconds

    async def register_failure(self, redis: Any, subject: str, ip: str | None = None) -> None:
        """记录一次登录失败：达到阈值即写入锁定标记。"""
        if self.max_failures <= 0:
            return
        await self._bump(redis, subject, self.max_failures)
        if self.ip_max_failures > 0 and ip:
            await self._bump(redis, f"ip:{ip}", self.ip_max_failures)

    async def _bump(self, redis: Any, subject: str, limit: int) -> None:
        if redis is None:
            return
        fail_key = _fail_key(subject)
        try:
            count = await redis.incr(fail_key)
            # 首次写入时补 TTL：否则失败计数永久累积，用户永远无法自行恢复
            if count == 1:
                await redis.expire(fail_key, self.lockout_seconds)
            if count >= limit:
                await redis.set(_lock_key(subject), "1", ex=self.lockout_seconds)
                await redis.delete(fail_key)
        except Exception:
            # 计数失败只告警：宁可放行也不能让 Redis 故障变成全站登录不可用
            logger.warning("记录登录失败次数失败，本次不计入锁定", exc_info=True)

    async def reset(self, redis: Any, subject: str, ip: str | None = None) -> None:
        """登录成功后清零失败计数与锁定标记（IP 维度保留，避免爆破者借成功登录洗白）。"""
        if redis is None or self.max_failures <= 0:
            return
        try:
            await redis.delete(_fail_key(subject), _lock_key(subject))
        except Exception:
            logger.warning("清空登录失败计数失败", exc_info=True)


def _locked_error(seconds: int) -> Exception:
    """锁定提示。

    数值不直接拼进文案：拼好之后就匹配不上词条表里的模板，翻译会永远失效。
    这里用「中文模板|参数」的写法，由异常处理层先翻译再插值（见
    app/plugins/i18n.py::translate_exception_detail）。
    """
    minutes = max(1, round(seconds / 60))
    detail = "登录失败次数过多，请 {minutes} 分钟后重试|" + json.dumps({"minutes": minutes})
    return HTTPException(status_code=429, detail=detail)


login_guard = LoginGuard()
