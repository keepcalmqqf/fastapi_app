from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# 导入所有模型，供 Alembic autogenerate 发现
from app.models.user import User  # noqa: E402, F401
