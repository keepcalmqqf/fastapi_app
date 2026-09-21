import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config.exception_handler import register_exception_handlers
from config.middleware import cors_middleware
from config.redis import create_redis
from controller.index_controller import router as index_router
from controller.user_controller import router as user_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = create_redis()
    yield
    await app.state.redis.aclose()


app = FastAPI(title="脚手架项目", lifespan=lifespan)

register_exception_handlers(app)

cors_middleware(app)

app.include_router(router=index_router, include_in_schema=False)
app.include_router(router=user_router)
