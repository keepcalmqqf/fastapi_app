"""国际化（i18n）插件：按配置开关挂载，关闭时行为与未集成完全一致。

设计取舍：
- 消息键沿用中文原文（如 "邮箱或密码错误"）。这样业务代码 `raise HTTPException(
  detail="...")` 的写法不变，中文环境返回内容逐字不变，存量测试与前端文案不受
  影响；需要其它语种时在词条表里补一份映射即可。
- 语言只按「显式参数 → Accept-Language → 默认」解析，不读 Cookie / 不写状态，
  避免给无状态 API 引入会话依赖。
- 翻译失败一律回退中文原文，绝不因为缺词条把接口变成 500 或空消息。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from pydantic import BaseModel

from app.api import API_V1_PREFIX
from app.core import result
from app.core.result import Result
from app.core.settings import settings

logger = logging.getLogger("fastapi_app")

# 当前请求语种；插件未启用时恒为 None
_current_locale: ContextVar[str | None] = ContextVar("i18n_locale", default=None)

# 内置词条表：键为中文原文，值为目标语种译文。
# 只列「会经统一响应返回给用户」的文案，日志与内部提示不在此列。
CATALOGS: dict[str, dict[str, str]] = {
    "zh-CN": {},
    "en-US": {
        "请求成功": "Success",
        "请求失败": "Request failed",
        "请求参数校验失败": "Invalid request parameters",
        "服务器内部错误": "Internal server error",
        "缓存服务异常，请稍后重试": "Cache service unavailable, please retry later",
        "数据库服务异常，请稍后重试": "Database service unavailable, please retry later",
        "未提供认证令牌": "Authentication token is missing",
        "认证令牌无效或已过期": "Authentication token is invalid or expired",
        "账号已被停用": "Account is disabled",
        "账号不存在": "Account does not exist",
        "邮箱或密码错误": "Invalid email or password",
        "邮箱未验证，请先完成邮箱验证": "Email is not verified",
        "未提供刷新令牌": "Refresh token is missing",
        "已退出登录": "Signed out",
        "登录成功": "Signed in",
        "令牌已刷新": "Token refreshed",
        "原密码不正确": "Current password is incorrect",
        "密码已修改，请重新登录": "Password updated, please sign in again",
        "权限不足": "Permission denied",
        "角色无权访问该资源": "Role is not allowed to access this resource",
        "用户不存在": "User not found",
        "邮箱已被注册": "Email is already registered",
        "不允许删除自己": "You cannot delete yourself",
        "不允许停用自己": "You cannot disable yourself",
        "角色不存在": "Role not found",
        "权限不存在": "Permission not found",
        "系统内置角色不允许删除": "Built-in roles cannot be deleted",
        "只有超级管理员可以分配超级管理员角色": (
            "Only a super administrator can assign the super administrator role"
        ),
        "会员不存在": "Member not found",
        "会员已注销": "Member account is closed",
        # 带插值参数的模板：参数由 detail 的 `|{...}` 部分提供（见 _split_params）
        "登录失败次数过多，请 {minutes} 分钟后重试": (
            "Too many failed sign-in attempts, please retry in {minutes} minutes"
        ),
        "权限不足，缺少：{missing}": "Permission denied, missing: {missing}",
        "{}不存在": "{} does not exist",
        "字段不能为空或纯空白": "Field cannot be blank",
        "密码必须同时包含字母和数字": "Password must contain both letters and digits",
        "登录失败次数过多，请稍后重试": "Too many failed sign-in attempts, please retry later",
        # 以下为各业务接口成功响应的文案（此前仅靠 auth.py 显式 translate=True 才翻译，
        # 其余接口的 message 默认不翻译；统一放开后需把用到的成功文案都补进词条表）
        "管理员初始化成功": "Admin initialized",
        "注册成功": "Registration successful",
        "邮箱验证成功": "Email verified",
        "邮箱已验证，无需重复发送": "Email already verified, no need to resend",
        "验证邮件已发送": "Verification email sent",
        "刷新成功": "Token refreshed",
        "退出登录成功": "Signed out",
        "更新成功": "Updated successfully",
        "注销成功": "Account closed",
        "删除成功": "Deleted successfully",
        "角色已更新": "Roles updated",
        "权限已更新": "Permissions updated",
        "创建成功": "Created successfully",
        # 异步任务插件：手动投递具名任务的成功文案
        "任务已投递": "Task dispatched",
        "服务正常": "Service is running",
        "部分依赖不可用": "Some dependencies are unavailable",
        # 失败响应文案（result.failure 现已默认翻译）
        "未授权访问": "Unauthorized access",
        "请求过于频繁，请稍后重试": "Too many requests, please try again later",
    },
}


class LocaleOut(BaseModel):
    supported: list[str]
    default: str


def supported_locales() -> list[str]:
    return list(settings.i18n_locale_list)


def default_locale() -> str:
    locales = supported_locales()
    if settings.I18N_DEFAULT_LOCALE in locales:
        return settings.I18N_DEFAULT_LOCALE
    return locales[0] if locales else "zh-CN"


def _normalize(candidate: str) -> str | None:
    """把 zh / zh_CN / zh-Hans-CN 之类写法归一到词条表里的语种；不支持则返回 None。"""
    value = candidate.strip().replace("_", "-")
    if not value:
        return None
    lowered = value.lower()
    for locale in supported_locales():
        if locale.lower() == lowered:
            return locale
    # 只匹配主语言（en → en-US）
    primary = lowered.split("-")[0]
    for locale in supported_locales():
        if locale.lower().split("-")[0] == primary:
            return locale
    return None


def resolve_locale(preferred: str | None = None, accept_language: str | None = None) -> str:
    """按「显式参数 → Accept-Language（含 q 权重）→ 默认」解析语种。"""
    if preferred:
        normalized = _normalize(preferred)
        if normalized:
            return normalized
    if accept_language:
        parsed: list[tuple[float, str]] = []
        for part in accept_language.split(","):
            item, _, raw_q = part.partition(";q=")
            try:
                quality = float(raw_q) if raw_q else 1.0
            except ValueError:
                quality = 1.0
            parsed.append((quality, item.strip()))
        for _quality, name in sorted(parsed, key=lambda p: p[0], reverse=True):
            normalized = _normalize(name)
            if normalized:
                return normalized
    return default_locale()


def get_locale() -> str:
    """当前请求语种；无请求上下文（如定时任务）时回退默认语种。"""
    return _current_locale.get() or default_locale()


def set_locale(locale: str) -> None:
    _current_locale.set(locale)


def catalog(locale: str) -> dict[str, str]:
    return CATALOGS.get(locale, {})


def translate(message: str, locale: str | None = None, **params: Any) -> str:
    """把中文原文翻译成目标语种；缺词条时原样返回。

    `params` 支持 `{}` 占位插值（如 "缺少：{missing}"）；插值失败不影响返回原文。
    """
    target = locale or get_locale()
    translated = catalog(target).get(message, message)
    if params:
        try:
            translated = translated.format(**params)
        except Exception:  # noqa: BLE001 - 词条与参数不匹配时宁可返回未插值文案
            logger.warning("i18n 插值失败: key=%s params=%s", message, params)
    return translated


def translate_exception_detail(detail: Any) -> str:
    """翻译 HTTPException 的 detail，支持「模板 + 参数」写法。

    带运行时数值的文案（如「请 {minutes} 分钟后重试」）一旦先把数值拼进字符串，
    就再也匹配不上词条表里的模板，只能永远返回中文。约定这类文案写成
    `detail="模板|" + json.dumps(params)`，由本函数拆出参数后再插值。
    没有参数时与 `translate()` 等价。
    """
    text = str(detail)
    params = _split_params(text)
    if params is None:
        return translate(text)
    template, _, _payload = text.rpartition("|")
    return translate(template, **params)


def _split_params(raw: str) -> dict[str, Any] | None:
    """从 `文案|{"k": v}` 里切出插值参数；格式不符时返回 None（按无参数处理）。"""
    if "|" not in raw:
        return None
    _text, _, payload = raw.rpartition("|")
    try:
        parsed = json.loads(payload)
    except Exception:  # noqa: BLE001
        return None
    return parsed if isinstance(parsed, dict) else None


# 模块级变量名不叫 router：api 包的自动发现会收集 app.api.* 下的 router，
# 这里若同名会被 register_routers 重复注册（且前缀不一致）。版本前缀从
# app.api 引入，保证与业务接口同源。
_router = APIRouter(prefix="/i18n", tags=["国际化"])


@_router.get("/locales", summary="支持的语种", response_model=Result[LocaleOut])
async def list_locales() -> Result[LocaleOut]:
    return result.ok(
        data=LocaleOut(supported=supported_locales(), default=default_locale()),
        message="请求成功",
    )


@_router.get("/messages", summary="获取语种词条", response_model=Result[dict])
async def get_messages(locale: str | None = None) -> Result[dict]:
    """返回该语种的完整词条（键为中文原文），供前端直接喂给 i18n 运行时。"""
    target = resolve_locale(preferred=locale)
    return result.ok(data=catalog(target), message="请求成功")


def setup(app: FastAPI) -> None:
    """挂载 i18n：注册词条接口 + 按请求解析语种。"""
    # 手动带版本前缀（插件不在 app.api 包内，不会被自动发现），与业务接口保持一致
    app.include_router(_router, prefix=API_V1_PREFIX)

    @app.middleware("http")
    async def _locale_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        locale = resolve_locale(
            preferred=request.query_params.get("locale"),
            accept_language=request.headers.get("Accept-Language"),
        )
        set_locale(locale)
        try:
            response = await call_next(request)
        finally:
            # 连接是可复用的，处理完必须清理，避免语种串到下一个请求
            _current_locale.set(None)
        response.headers["Content-Language"] = locale
        return response

    logger.info(
        "插件已启用: i18n (默认 %s，支持 %s)", default_locale(), ", ".join(supported_locales())
    )
