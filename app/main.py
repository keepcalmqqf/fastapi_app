import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from app.api import register_routers
from app.core.database import async_engine
from app.core.exceptions import register_exception_handlers
from app.core.middleware import cors_middleware, response_headers_middleware
from app.core.redis import create_redis
from app.core.settings import settings
from app.core.static import SPAStaticFiles
from app.plugins import aclose_plugins, setup_plugins
from app.plugins.cache import set_redis
from app.plugins.scheduler import scheduler
from app.plugins.tasks import task_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fastapi_app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis = create_redis()
    app.state.redis = redis
    set_redis(redis)
    try:
        # 异步任务插件：Redis/DB 就绪后再启动 worker；关闭时先停 worker（在 Redis 仍可用时
        # 让在途任务自然收尾），最后才关闭资源
        if settings.ENABLE_TASKS:
            await task_manager.start()
        # 定时任务插件在 task_manager 之后启动（到点投递依赖它就绪）；关闭时先停调度
        # （不再产生新投递），再停 task_manager 让在途任务收尾
        if settings.ENABLE_SCHEDULER:
            await scheduler.start()
        yield
    finally:
        # 启动阶段在 yield 之前抛异常时，asynccontextmanager 不会执行 yield 之后的
        # 任何语句——没有 try/finally 就会漏掉 redis.aclose() 与 engine.dispose()，
        # 连接与后台协程一并泄漏，且启动失败的进程可能残留到进程退出。
        #
        # 清理的每一步都独立容错：任一步抛异常只记录，不能跳过其后的清理，否则在途
        # 任务与连接池会连带泄漏。启动阶段的异常照原样向上传播（只记不吞），这样
        # 启动失败会显式暴露，而不是带着「配了却没起来」的任务插件静默对外服务。
        for label, stop in (
            ("定时任务调度器", scheduler.stop if settings.ENABLE_SCHEDULER else None),
            ("任务 worker", task_manager.stop if settings.ENABLE_TASKS else None),
        ):
            if stop is None:
                continue
            try:
                await stop()
            except Exception:
                logger.exception("关闭%s失败，继续清理其余资源", label)
        # 插件自带的连接池（如限流的 Redis 存储）不在 app.state.redis 里
        try:
            await aclose_plugins(app)
        except Exception:
            logger.exception("关闭插件资源失败，继续清理其余资源")
        for label, closer in (
            ("Redis 客户端", redis.aclose),
            ("数据库连接池", async_engine.dispose),
        ):
            try:
                await closer()
            except Exception:
                logger.exception("关闭%s失败", label)


_enable_docs = settings.docs_enabled
app = FastAPI(
    title="脚手架项目",
    lifespan=lifespan,
    docs_url="/docs" if _enable_docs else None,
    openapi_url="/openapi.json" if _enable_docs else None,
    redoc_url="/redoc" if _enable_docs else None,
)

register_exception_handlers(app)
cors_middleware(app)
response_headers_middleware(app)
# 只压缩超过 1KB 的响应：小响应压缩收益低却同样带来 CPU 开销与 BREACH 面
app.add_middleware(GZipMiddleware, minimum_size=1000)
setup_plugins(app)
register_routers(app)

# 前端构建产物存在时由后端托管（API 路由注册在前，优先匹配，不受影响）
_frontend_dist = Path(settings.FRONTEND_DIST_DIR)
if _frontend_dist.is_dir():
    app.mount("/", SPAStaticFiles(directory=_frontend_dist, html=True), name="frontend")
