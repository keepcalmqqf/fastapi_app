import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from util import result

logger = logging.getLogger("fastapi_app")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            content=result.failure(code=422, message=exc.errors()[0]["msg"]))

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=result.failure(code=exc.status_code, message=str(exc.detail)))

    @app.exception_handler(RedisError)
    async def redis_exception_handler(request: Request, exc: RedisError):
        logger.exception("Redis 操作失败: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=result.failure(message=f"Redis 服务异常({type(exc).__name__})，请检查 Redis 是否已启动"))

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
        logger.exception("数据库操作失败: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=result.failure(message=f"数据库服务异常({type(exc).__name__})，请检查数据库是否已启动"))

    @app.exception_handler(Exception)
    async def unknown_exception_handler(request: Request, exc: Exception):
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=result.failure(message=f"服务器内部错误({type(exc).__name__})"))
