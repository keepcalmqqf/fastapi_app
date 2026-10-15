from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import result
from app.core.database import get_db
from app.core.deps import get_current_user, get_redis
from app.core.result import Result
from app.core.revocation import revoke_subject
from app.core.settings import settings
from app.models import User
from app.schemas.token import TokenOut
from app.schemas.user import ChangePassword, UserLogin
from app.services import audit as audit_service
from app.services import tokens as token_service
from app.services import user as user_service
from app.services.audit import ACTION_CHANGE_PASSWORD, ACTION_LOGIN_FAILED

router = APIRouter(prefix="/auth", tags=["认证"])


class RefreshBody(BaseModel):
    """刷新令牌兜底请求体（优先使用 refresh_token Cookie）。"""

    refresh_token: str | None = None


@router.post("/login", summary="登录并获取访问令牌", response_model=Result[TokenOut])
async def login(
    data: UserLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Any = Depends(get_redis),
) -> Result[TokenOut]:
    user = await user_service.authenticate(db, data.email, data.password)
    if user is None:
        # 登录失败要留痕：连续失败是撞库/爆破的信号（限流仅按 IP 计数，审计可按时段复盘）
        await audit_service.record(
            db,
            actor_type="system",
            actor_id=None,
            action=ACTION_LOGIN_FAILED,
            target_type="user",
            target_id=data.email,
            ip=audit_service.client_ip(request),
        )
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    if settings.REQUIRE_EMAIL_VERIFICATION and not user.email_verified:
        raise HTTPException(status_code=403, detail="邮箱未验证，请先完成邮箱验证")
    token_out = await token_service.issue_tokens(str(user.id), "admin", response, redis)
    return result.ok(data=token_out, message="登录成功")


@router.post("/refresh", summary="刷新访问令牌", response_model=Result[TokenOut])
async def refresh(
    request: Request,
    response: Response,
    body: RefreshBody | None = None,
    redis: Any = Depends(get_redis),
) -> Result[TokenOut]:
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token and body is not None:
        refresh_token = body.refresh_token
    if not refresh_token:
        raise HTTPException(status_code=401, detail="未提供刷新令牌")
    token_out = await token_service.rotate_refresh_token(refresh_token, response, redis)
    return result.ok(data=token_out, message="令牌已刷新")


@router.post("/logout", summary="登出并撤销令牌", response_model=Result[None])
async def logout(
    request: Request, response: Response, redis: Any = Depends(get_redis)
) -> Result[None]:
    # access 令牌：Authorization Bearer 优先，其次 access_token Cookie
    access_token = request.cookies.get("access_token")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        access_token = auth[7:].strip() or access_token
    refresh_token = request.cookies.get("refresh_token")
    await token_service.revoke_tokens(access_token, refresh_token, redis)
    # path 需与写入时一致，否则浏览器删不掉 Cookie
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return result.ok(message="已退出登录")


@router.post("/change-password", summary="修改当前登录用户密码", response_model=Result[None])
async def change_password(
    data: ChangePassword,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Any = Depends(get_redis),
) -> Result[None]:
    changed = await user_service.change_password(
        db, current_user, data.old_password, data.new_password
    )
    if not changed:
        raise HTTPException(status_code=400, detail="原密码不正确")
    # 凭证已变更：终止该账号的全部会话（含其他设备的登录态）。
    # 撤销按签发时间判定，当前令牌同样失效，符合「改密后重新登录」的通行做法。
    await revoke_subject("admin", str(current_user.id), redis)
    await audit_service.record(
        db,
        actor_type="admin",
        actor_id=current_user.id,
        action=ACTION_CHANGE_PASSWORD,
        target_type="user",
        target_id=str(current_user.id),
        ip=audit_service.client_ip(request),
    )
    return result.ok(message="密码已修改，请重新登录")
