# coding: utf-8
from sqlalchemy import Integer
from sqlalchemy.dialects.mysql import TINYINT, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'user'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str | None] = mapped_column(VARCHAR(32))
    email: Mapped[str | None] = mapped_column(VARCHAR(32), unique=True)
    password: Mapped[str | None] = mapped_column(VARCHAR(32))
    is_active: Mapped[bool | None] = mapped_column(TINYINT(1))
