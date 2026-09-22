from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """审计时间戳公共混入：created_at 入库时由数据库生成，updated_at 额外在行更新时刷新。"""

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    """软删除公共混入：deleted_at 为空表示记录有效，非空表示已删除。

    查询侧统一由 repository 过滤 deleted_at.is_(None)，删除只置时间戳不物理删行。
    取舍说明：软删除不释放邮箱唯一约束，已删账号的邮箱不可再注册（并发下由
    IntegrityError 兜底转 409）。
    """

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)
