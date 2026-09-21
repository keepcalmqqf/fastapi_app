from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Member(Base):
    """会员（C 端用户），与后台用户 User 分表、分令牌。"""

    __tablename__ = "member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nickname: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    password: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
