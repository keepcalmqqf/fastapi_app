import logging
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

logger = logging.getLogger("fastapi_app")

T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    code: int = 200
    data: T | None = None
    message: str = "请求成功"


def _translate(message: str, translate_flag: bool) -> str:
    """按当前请求语种翻译文案。

    `translate_flag` 为 False 时完全不触碰文案：i18n 插件未启用时保持逐字返回，
    行为与未集成国际化一致。翻译链路的任何异常都回退原文，绝不因缺词条让接口失败。
    """
    if not translate_flag:
        return message
    try:
        from app.plugins.i18n import get_locale, translate

        return translate(message, locale=get_locale())
    except Exception:  # noqa: BLE001
        logger.warning("文案翻译失败，回退原文: %s", message, exc_info=True)
        return message


def ok(
    data: T | None = None,
    message: str = "请求成功",
    code: int = 200,
    *,
    translate: bool = False,
    params: dict[str, Any] | None = None,
) -> Result[T]:
    """构造成功响应。

    `translate=True` 时把文案按当前请求语种翻译（需 i18n 插件已启用），
    `params` 用于词条里的 `{}` 占位插值。
    """

    if params:
        try:
            message = message.format(**params)
        except Exception:  # noqa: BLE001
            logger.warning("文案插值失败，保留原样: %s", message)
    return Result(code=code, data=data, message=_translate(message, translate))


def failure(code: int = 500, message: str = "请求失败") -> Result[None]:
    return Result(code=code, data=None, message=message)
