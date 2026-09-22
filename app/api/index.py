from fastapi import APIRouter
from pydantic import BaseModel

from app.core import result
from app.core.result import Result

router = APIRouter()


class AppInfoOut(BaseModel):
    service: str
    docs: str


@router.get("/api/info", response_model=Result[AppInfoOut])
async def index():
    return result.ok(data=AppInfoOut(service="fastapi_app", docs="/docs"))
