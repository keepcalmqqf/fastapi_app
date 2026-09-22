from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _normalize_email(value: str) -> str:
    """统一邮箱大小写与首尾空白，避免唯一性被大小写绕过（SQLite 等大小写敏感场景）。"""
    return value.lower().strip()


def _validate_password_strength(value: str) -> str:
    """密码必须同时包含字母和数字。"""
    if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
        raise ValueError("密码必须同时包含字母和数字")
    return value


def _validate_non_blank(value: str) -> str:
    """字段去空白后不能为空（挡住纯空白字符串）。"""
    if not value.strip():
        raise ValueError("字段不能为空或纯空白")
    return value


class CreateUser(BaseModel):
    name: str = Field(min_length=2, max_length=32)
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)
    is_active: bool = True

    _normalize_email = field_validator("email", mode="before")(_normalize_email)
    _check_password = field_validator("password")(_validate_password_strength)
    _check_name = field_validator("name")(_validate_non_blank)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(max_length=64)

    _normalize_email = field_validator("email", mode="before")(_normalize_email)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    email: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
