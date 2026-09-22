from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core import result
from app.core.database import get_db
from app.core.deps import bearer_scheme, get_current_user, get_redis
from app.core.result import Result
from app.models import User
from app.schemas.page import Page, PageParams
from app.schemas.user import CreateUser, UpdateUser, UserOut
from app.services import user as user_service

router = APIRouter(prefix="/user", tags=["用户"])


@router.get("/get_user", summary="通过用户id获取用户", response_model=Result[UserOut])
def get_user_by_id(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    user = user_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return result.ok(data=UserOut.model_validate(user))


@router.get("/list", summary="分页获取用户列表", response_model=Result[Page[UserOut]])
def list_users(
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    items, total = user_service.list_users(db, params.page, params.page_size)
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
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
):
    # 空库自举：首个管理员可无凭证创建；表非空时必须持有有效后台令牌。
    # get_current_user 是 async 依赖，这里在端点内手动 await（传入 request 以支持
    # Cookie 来源令牌及其 CSRF 头校验，行为与依赖注入路径一致）。
    if not user_service.needs_bootstrap(db):
        await get_current_user(request=request, credentials=credentials, db=db, redis=redis)
    created = user_service.create_user(db, user)
    if created is None:
        raise HTTPException(status_code=409, detail="邮箱已被注册")
    return result.ok(data=UserOut.model_validate(created), message="添加成功")


@router.get("/me", summary="获取当前登录用户", response_model=Result[UserOut])
def get_me(current_user: User = Depends(get_current_user)):
    return result.ok(data=UserOut.model_validate(current_user))


@router.patch("/{user_id}", summary="更新用户", response_model=Result[UserOut])
def update_user(
    user_id: int,
    data: UpdateUser,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    user = user_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    updated = user_service.update_user(db, user, data.model_dump(exclude_unset=True))
    return result.ok(data=UserOut.model_validate(updated), message="更新成功")


@router.delete("/{user_id}", summary="删除用户（软删除）", response_model=Result[None])
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不允许删除当前登录用户")
    user = user_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user_service.delete_user(db, user)
    return result.ok(message="删除成功")
