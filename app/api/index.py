from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import result
from app.core.database import get_db
from app.core.result import Result
from app.services import user as user_service

# 路径不带 /api 前缀：register_routers 会统一加 /api/v1，最终路径为 /api/v1/info
router = APIRouter()


class AppInfoOut(BaseModel):
    service: str
    docs: str
    # 是否仍需初始化首个管理员：前端据此决定「注册/初始化」入口是否展示。
    # 这是免鉴权接口，只暴露布尔状态，不泄露账号数量等其它信息
    needs_bootstrap: bool


@router.get("/info", response_model=Result[AppInfoOut])
async def index(db: AsyncSession = Depends(get_db)) -> Result[AppInfoOut]:
    needs = await user_service.needs_bootstrap(db)
    return result.ok(
        data=AppInfoOut(service="fastapi_app", docs="/docs", needs_bootstrap=needs),
        message="请求成功",
    )
