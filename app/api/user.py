from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import result
from app.core.database import get_db
from app.core.deps import bearer_scheme, get_current_user, get_redis
from app.core.result import Result
from app.core.revocation import revoke_subject
from app.models import User
from app.schemas.page import Page, PageParams
from app.schemas.user import CreateUser, UpdateUser, UserOut
from app.services import user as user_service

router = APIRouter(prefix="/user", tags=["用户"])


@router.get("/get_user", summary="通过用户id获取用户", response_model=Result[UserOut])
async def get_user_by_id(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    user = await user_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return result.ok(data=UserOut.model_validate(user))


@router.get("/list", summary="分页获取用户列表", response_model=Result[Page[UserOut]])
async def list_users(
    params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    items, total = await user_service.list_users(db, params.page, params.page_size)
    page = Page[UserOut](
        total=total,
        page=params.page,
        page_size=params.page_size,
        items=[UserOut.model_validate(u) for u in items],
    )
    return result.ok(data=page)


@router.post("/create_user", summary="创建用户", response_model=Result[UserOut])
async def create_user(
    user: CreateUser,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    # 空库自举：首个管理员可无凭证创建；表非空时必须持有有效后台令牌。
    # get_current_user 是 async 依赖，这里在端点内手动 await（传入 request 以支持
    # Cookie 来源令牌及其 CSRF 头校验，行为与依赖注入路径一致）。
    if not await user_service.needs_bootstrap(db):
        await get_current_user(request=request, credentials=credentials, db=db, redis=redis)
    created = await user_service.create_user(db, user)
    if created is None:
        raise HTTPException(status_code=409, detail="邮箱已被注册")
    return result.ok(data=UserOut.model_validate(created), message="添加成功")


@router.get("/me", summary="获取当前登录用户", response_model=Result[UserOut])
async def get_me(current_user: User = Depends(get_current_user)):
    return result.ok(data=UserOut.model_validate(current_user))


@router.patch("/{user_id}", summary="更新用户", response_model=Result[UserOut])
async def update_user(
    user_id: int,
    data: UpdateUser,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    redis=Depends(get_redis),
):
    user = await user_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    fields = data.model_dump(exclude_unset=True)
    updated = await user_service.update_user(db, user, fields)
    # 停用账号 = 终止其全部会话：撤销该主体所有已签发令牌，避免旧令牌在有效期内继续可用。
    # 重新启用不会复活旧会话（撤销标记按签发时间判定），用户重新登录即可获得新令牌。
    if fields.get("is_active") is False:
        await revoke_subject("admin", str(user_id), redis)
    return result.ok(data=UserOut.model_validate(updated), message="更新成功")


@router.delete("/{user_id}", summary="删除用户（软删除）", response_model=Result[None])
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis=Depends(get_redis),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不允许删除当前登录用户")
    user = await user_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    await user_service.delete_user(db, user)
    # 软删除后撤销该账号全部令牌；软删不释放邮箱唯一约束，该账号不会再产生新会话
    await revoke_subject("admin", str(user_id), redis)
    return result.ok(message="删除成功")
