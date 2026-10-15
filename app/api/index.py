from fastapi import APIRouter
from pydantic import BaseModel

from app.core import result
from app.core.result import Result

# 路径不带 /api 前缀：register_routers 会统一加 /api/v1，最终路径为 /api/v1/info
router = APIRouter()


class AppInfoOut(BaseModel):
    service: str
    docs: str


@router.get("/info", response_model=Result[AppInfoOut])
async def index() -> Result[AppInfoOut]:
    return result.ok(data=AppInfoOut(service="fastapi_app", docs="/docs"))
