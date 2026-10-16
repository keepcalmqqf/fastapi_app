import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import result

logger = logging.getLogger("fastapi_app")


def _t(message: str, **params: Any) -> str:
    """按当前请求语种翻译文案；i18n 插件未启用时原样返回。"""
    try:
        from app.plugins.i18n import translate
    except ImportError:  # pragma: no cover - 插件模块始终存在，兜底防导入环
        return message
    return translate(message, **params)


def _t_detail(detail: Any) -> str:
    """翻译 HTTPException 的 detail（支持「模板|参数」写法，见插件的文档字符串）。"""
    try:
        from app.plugins.i18n import translate_exception_detail
    except ImportError:  # pragma: no cover
        return str(detail)
    return translate_exception_detail(detail)


def _validation_response(exc: ValidationError | RequestValidationError) -> JSONResponse:
    # 逐条翻译校验消息：命中词条表才替换，未命中保留 pydantic 原文
    errors = [
        {"loc": list(e.get("loc", [])), "msg": _t(str(e.get("msg", "")))} for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=result.Result(
            code=422, data={"errors": errors}, message=_t("请求参数校验失败")
        ).model_dump(),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _validation_response(exc)

    @app.exception_handler(ValidationError)
    async def pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
        """兜底：类依赖内部的跨字段校验错误不走标准参数校验流程。

        用 `Depends()` 挂载的模型依赖（如 PageParams、AuditLogQuery）在构造时抛出的
        ValidationError，只有字段级错误会被 FastAPI 包装成 RequestValidationError；
        model_validator 这类跨字段校验的错误会原样冒泡，没有本兜底就会掉进通用
        Exception 处理器变成 500——调用方看到的是「服务器内部错误」而非参数问题。
        """
        logger.warning("请求参数校验失败: %s %s", request.method, request.url.path)
        return _validation_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        content = result.failure(code=exc.status_code, message=_t_detail(exc.detail)).model_dump()
        if exc.headers:
            return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RedisError)
    async def redis_exception_handler(request: Request, exc: RedisError) -> JSONResponse:
        logger.exception("Redis 操作失败: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=result.failure(message=_t("缓存服务异常，请稍后重试")).model_dump(),
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("数据库操作失败: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=result.failure(message=_t("数据库服务异常，请稍后重试")).model_dump(),
        )

    @app.exception_handler(Exception)
    async def unknown_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=result.failure(message=_t("服务器内部错误")).model_dump(),
        )
