import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from app.api import register_routers
from app.core.database import engine
from app.core.exceptions import register_exception_handlers
from app.core.middleware import cors_middleware
from app.core.redis import create_redis
from app.core.settings import settings
from app.core.static import SPAStaticFiles
from app.plugins import setup_plugins
from app.plugins.cache import set_redis

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = create_redis()
    app.state.redis = redis
    set_redis(redis)
    yield
    await redis.aclose()
    engine.dispose()


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
app.add_middleware(GZipMiddleware)
setup_plugins(app)
register_routers(app)

# 前端构建产物存在时由后端托管（API 路由注册在前，优先匹配，不受影响）
_frontend_dist = Path(settings.FRONTEND_DIST_DIR)
if _frontend_dist.is_dir():
    app.mount("/", SPAStaticFiles(directory=_frontend_dist, html=True), name="frontend")
