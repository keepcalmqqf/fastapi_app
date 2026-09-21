from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import Member, User
from app.repositories import member as member_repo
from app.repositories import user as user_repo

bearer_scheme = HTTPBearer(auto_error=False)


def _resolve_subject(credentials: HTTPAuthorizationCredentials | None, audience: str) -> str:
    """校验令牌并返回 subject；audience 不匹配（如会员令牌访问后台接口）同样视为无效。"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    payload = decode_access_token(credentials.credentials)
    subject = payload.get("sub") if payload else None
    # 旧版令牌没有 aud 字段，按 admin 兼容处理
    token_aud = payload.get("aud", "admin") if payload else None
    if token_aud != audience or not subject or not subject.isdigit():
        raise HTTPException(status_code=401, detail="认证令牌无效或已过期")
    return subject


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    subject = _resolve_subject(credentials, "admin")
    user = user_repo.get_user_by_id(db, int(subject))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    return user


def get_current_member(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Member:
    subject = _resolve_subject(credentials, "member")
    member = member_repo.get_member_by_id(db, int(subject))
    if member is None or not member.is_active:
        raise HTTPException(status_code=401, detail="会员不存在或已禁用")
    return member
