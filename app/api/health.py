from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core import result
from app.core.database import async_engine
from app.core.result import Result

router = APIRouter(tags=["系统"])


@router.get("/health", summary="健康检查（数据库 + Redis）", response_model=Result[dict])
async def health(request: Request) -> JSONResponse:
    components: dict[str, str] = {}

    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        components["mysql"] = "ok"
    except Exception:
        components["mysql"] = "down"

    try:
        await request.app.state.redis.ping()
        components["redis"] = "ok"
    except Exception:
        components["redis"] = "down"

    healthy = all(v == "ok" for v in components.values())
    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content=result.ok(
            code=status_code,
            data={"status": "ok" if healthy else "degraded", "components": components},
            message="服务正常" if healthy else "部分依赖不可用",
        ).model_dump(),
    )


@router.get("/health/live", summary="存活探活（不探依赖）", response_model=Result[dict])
async def health_live() -> Result[dict]:
    """只表示进程本身存活，不探 MySQL/Redis。

    容器探活应打这个端点：依赖短暂抖动不应把容器整体判为不健康，否则编排层可能
    据此摘流量或重启，把一次 Redis 抖动放大成全部副本重启。
    """
    return result.ok(data={"status": "ok"}, message="服务正常")


@router.get("/health/ready", summary="就绪检查（探依赖）", response_model=Result[dict])
async def health_ready(request: Request) -> JSONResponse:
    """依赖全部可用才算就绪，供网关/编排做流量摘除判断。"""
    return await health(request)
