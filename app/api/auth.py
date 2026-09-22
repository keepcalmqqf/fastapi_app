from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import result
from app.core.database import get_db
from app.core.deps import get_redis
from app.core.result import Result
from app.schemas.token import TokenOut
from app.schemas.user import UserLogin
from app.services import tokens as token_service
from app.services import user as user_service

router = APIRouter(prefix="/auth", tags=["认证"])


class RefreshBody(BaseModel):
    """刷新令牌兜底请求体（优先使用 refresh_token Cookie）。"""

    refresh_token: str | None = None


@router.post("/login", summary="登录并获取访问令牌", response_model=Result[TokenOut])
async def login(
    data: UserLogin,
    response: Response,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
):
    user = user_service.authenticate(db, data.email, data.password)
    if user is None:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token_out = await token_service.issue_tokens(str(user.id), "admin", response, redis)
    return result.ok(data=token_out, message="登录成功")


@router.post("/refresh", summary="刷新访问令牌", response_model=Result[TokenOut])
async def refresh(
    request: Request,
    response: Response,
    body: RefreshBody | None = None,
    redis=Depends(get_redis),
):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token and body is not None:
        refresh_token = body.refresh_token
    if not refresh_token:
        raise HTTPException(status_code=401, detail="未提供刷新令牌")
    token_out = await token_service.rotate_refresh_token(refresh_token, response, redis)
    return result.ok(data=token_out, message="令牌已刷新")


@router.post("/logout", summary="登出并撤销令牌", response_model=Result[None])
async def logout(request: Request, response: Response, redis=Depends(get_redis)):
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
