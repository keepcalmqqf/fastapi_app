import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import register_routers
from app.core.exceptions import register_exception_handlers
from app.core.middleware import cors_middleware
from app.core.redis import create_redis
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


app = FastAPI(title="脚手架项目", lifespan=lifespan)

register_exception_handlers(app)
cors_middleware(app)
setup_plugins(app)
register_routers(app)
