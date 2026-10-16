import logging
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

logger = logging.getLogger("fastapi_app")

T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    code: int = 200
    data: T | None = None
    message: str = "请求成功"


def _translate(
    message: str,
    translate_flag: bool,
    params: dict[str, Any] | None = None,
) -> str:
    """按当前请求语种翻译文案。

    `translate_flag` 为 False 时完全不触碰文案：需要显式跳过翻译的场景（如插件自身的
    词条接口）可用它关闭。`params` 走词条模板的 `{}` 占位插值（见 i18n.translate）。
    翻译链路的任何异常都回退原文，绝不因缺词条让接口失败。
    """
    if not translate_flag:
        return message
    try:
        from app.plugins.i18n import get_locale, translate

        return translate(message, locale=get_locale(), **(params or {}))
    except Exception:  # noqa: BLE001
        logger.warning("文案翻译失败，回退原文: %s", message, exc_info=True)
        return message


def ok(
    data: T | None = None,
    message: str = "请求成功",
    code: int = 200,
    *,
    translate: bool = True,
    params: dict[str, Any] | None = None,
) -> Result[T]:
    """构造成功响应。

    `translate=True`（默认）时把文案按当前请求语种翻译（需 i18n 插件已启用并由请求
    中间件解析出语种）。翻译是尽力而为：缺词条或缺 i18n 时原样返回中文，绝不因翻译
    失败让接口变 500。`params` 用于词条里的 `{}` 占位插值，先翻译模板再插值。
    """

    return Result(code=code, data=data, message=_translate(message, translate, params))


def failure(code: int = 500, message: str = "请求失败", *, translate: bool = True) -> Result[None]:
    """构造失败响应；默认同样按当前请求语种翻译文案。"""
    return Result(code=code, data=None, message=_translate(message, translate))
