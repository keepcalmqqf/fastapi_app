from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin


class AuditLog(Base, TimestampMixin):
    """审计日志：谁在什么时候对什么做了什么。

    只记录写操作与安全相关事件（创建/更新/删除账号、改密、登录失败）。
    不挂 SoftDeleteMixin——审计记录本身不允许删除，软删在这里没有意义。
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 操作者身份类型：admin / member / system
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 操作者 id；system 或未登录场景（如登录失败）为 None
    actor_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # 动作标识，如 user.create / user.delete / auth.login_failed
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 目标对象类型与 id（id 存字符串，兼容非整型主键）
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64))
    # 变更要点（JSON 字符串，如 {"is_active": false}）
    detail: Mapped[str | None] = mapped_column(String(512))
    # 来源 IP（直连场景取 client.host；反向代理后应改为读取 X-Forwarded-For）
    ip: Mapped[str | None] = mapped_column(String(64))
