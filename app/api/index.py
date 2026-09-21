from fastapi import APIRouter

from app.core import result

router = APIRouter()


@router.get("/api/info")
async def index():
    return result.ok(data={"service": "fastapi_app", "docs": "/docs"})
