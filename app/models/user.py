from sqlalchemy import Boolean, Integer, String, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.rbac import user_role_table


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 业务上必填，故列也设为 NOT NULL：此前可空会让「缺字段的行」能落库，
    # 而 schema 层却要求必填，两处契约不一致
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # 邮箱是否已验证；是否强制验证后才允许登录由 REQUIRE_EMAIL_VERIFICATION 控制
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    # 用户所属角色（多对多）；权限由其全部角色聚合得出
    roles: Mapped[list["Role"]] = relationship(  # noqa: F821
        "Role", secondary=user_role_table, back_populates="users"
    )
