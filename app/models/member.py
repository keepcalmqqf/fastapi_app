from sqlalchemy import Boolean, Integer, String, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class Member(Base, TimestampMixin, SoftDeleteMixin):
    """会员（C 端用户），与后台用户 User 分表、分令牌。"""

    __tablename__ = "member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 同 User：业务必填的列在库里也应为 NOT NULL
    nickname: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # 邮箱是否已验证；是否强制验证后才允许登录由 REQUIRE_EMAIL_VERIFICATION 控制
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
