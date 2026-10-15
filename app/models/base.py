from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


def utc_now() -> datetime:
    """应用侧 UTC 当前时间（naive，匹配不带时区的 DATETIME 列）。

    时间戳必须由单一来源生成：此前 created_at / updated_at 用数据库侧的
    `func.now()`（取数据库服务器时区），而软删除用 Python 的 UTC 时间，
    两侧时区不一致时（MySQL 服务器跑在本地时区很常见）created_at 与 deleted_at
    会相差若干小时，按时间范围过滤软删记录就会出错。

    因此 ORM 路径一律由本函数生成 UTC 时间；`server_default` 仅保留给
    手工 SQL 插入与历史行兜底。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    """审计时间戳公共混入：created_at 入库时生成，updated_at 额外在行更新时刷新。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class SoftDeleteMixin:
    """软删除公共混入：deleted_at 为空表示记录有效，非空表示已删除。

    查询侧统一由 repository 过滤 deleted_at.is_(None)，删除只置时间戳不物理删行。
    取舍说明：软删除不释放邮箱唯一约束，已删账号的邮箱不可再注册（并发下由
    IntegrityError 兜底转 409）。
    """

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)
