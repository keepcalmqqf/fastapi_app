import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import result

logger = logging.getLogger("fastapi_app")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(
            status_code=422,
            content=result.Result(
                code=422, data={"errors": errors}, message="请求参数校验失败"
            ).model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        content = result.failure(code=exc.status_code, message=str(exc.detail)).model_dump()
        if exc.headers:
            return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RedisError)
    async def redis_exception_handler(request: Request, exc: RedisError):
        logger.exception("Redis 操作失败: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=result.failure(message="缓存服务异常，请稍后重试").model_dump(),
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
        logger.exception("数据库操作失败: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=result.failure(message="数据库服务异常，请稍后重试").model_dump(),
        )

    @app.exception_handler(Exception)
    async def unknown_exception_handler(request: Request, exc: Exception):
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=result.failure(message="服务器内部错误").model_dump(),
        )
