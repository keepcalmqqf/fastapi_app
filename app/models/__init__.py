from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# 导入所有模型，供 Alembic autogenerate 发现
from app.models.audit import AuditLog  # noqa: E402, F401
from app.models.member import Member  # noqa: E402, F401
from app.models.rbac import (  # noqa: E402, F401
    Permission,
    Role,
    role_permission_table,
    user_role_table,
)
from app.models.user import User  # noqa: E402, F401
