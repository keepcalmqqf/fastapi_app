from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_code(value: str) -> str:
    """code 仅允许字母数字与下划线/冒号，避免注入式命名与展示错乱。"""
    if not all(ch.isalnum() or ch in "_:" for ch in value):
        raise ValueError("code 只能包含字母、数字、下划线和冒号")
    return value


def _validate_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("字段不能为空或纯空白")
    return value


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    resource: str
    action: str
    description: str
    created_at: datetime
    updated_at: datetime


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    description: str
    is_system: bool
    permissions: list[PermissionOut] = []
    created_at: datetime
    updated_at: datetime


class CreateRole(BaseModel):
    name: str = Field(min_length=2, max_length=32)
    code: str = Field(min_length=2, max_length=32)
    description: str = ""

    _check_name = field_validator("name")(_validate_non_blank)
    _check_code = field_validator("code")(_validate_code)


class UpdateRole(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=32)
    description: str | None = None

    _check_name = field_validator("name")(_validate_non_blank)


class CreatePermission(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    code: str = Field(min_length=2, max_length=64)
    resource: str = ""
    action: str = ""
    description: str = ""

    _check_name = field_validator("name")(_validate_non_blank)
    _check_code = field_validator("code")(_validate_code)


class SetRolePermissionsIn(BaseModel):
    """整组替换角色权限：传入权限 code 列表，未知 code 会被忽略。"""

    permission_codes: list[str] = Field(default_factory=list, max_length=200)


class SetUserRolesIn(BaseModel):
    """整组替换用户角色：传入角色 code 列表，未知 code 会被忽略。"""

    role_codes: list[str] = Field(default_factory=list, max_length=50)


class UserPermissionsOut(BaseModel):
    """当前登录用户拥有的角色与权限 code 集合，供前端菜单/按钮级鉴权使用。"""

    role_codes: list[str] = []
    permission_codes: list[str] = []
