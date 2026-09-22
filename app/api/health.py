from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core import result
from app.core.database import engine
from app.core.result import Result

router = APIRouter(tags=["系统"])


@router.get("/health", summary="健康检查（数据库 + Redis）", response_model=Result[dict])
async def health(request: Request):
    components: dict[str, str] = {}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
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
