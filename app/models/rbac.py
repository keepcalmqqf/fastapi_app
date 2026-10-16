from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base
from app.models.base import SoftDeleteMixin, TimestampMixin

# 用户 ↔ 角色关联表（无独立模型，仅描述多对多关系；级联删除避免遗留脏数据）
user_role_table = Table(
    "user_role",
    Base.metadata,
    Column(
        "user_id",
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        Integer,
        ForeignKey("role.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# 角色 ↔ 权限关联表
role_permission_table = Table(
    "role_permission",
    Base.metadata,
    Column(
        "role_id",
        Integer,
        ForeignKey("role.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        Integer,
        ForeignKey("permission.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Role(Base, TimestampMixin, SoftDeleteMixin):
    """后台角色；code 为程序内判定依据（如 super_admin），name 仅展示。"""

    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", server_default="")
    # 系统内置角色（超级管理员/管理员/访客）不可删除、不可改 code，
    # 避免把支撑系统运转的角色误删后权限体系失稳
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    users: Mapped[list["User"]] = relationship(  # noqa: F821
        "User", secondary=user_role_table, back_populates="roles"
    )
    permissions: Mapped[list["Permission"]] = relationship(
        "Permission", secondary=role_permission_table, back_populates="roles"
    )


class Permission(Base, TimestampMixin, SoftDeleteMixin):
    """后台权限点；code 形如 `user:read`，由 resource + action 两段组成便于归类。"""

    __tablename__ = "permission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    resource: Mapped[str] = mapped_column(String(32), default="", server_default="")
    action: Mapped[str] = mapped_column(String(32), default="", server_default="")
    description: Mapped[str] = mapped_column(String(255), default="", server_default="")

    roles: Mapped[list["Role"]] = relationship(
        "Role", secondary=role_permission_table, back_populates="permissions"
    )
