from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


class CreateUser(BaseModel):
    name: str
    email: EmailStr
    password: str
    is_active: bool

    @field_validator("name")
    @classmethod
    def validator_name(cls, value: str) -> str:
        if not 2 <= len(value) <= 10:
            raise ValueError("姓名长度应不小于2且不大于10")
        return value

    @field_validator("password")
    @classmethod
    def validator_password(cls, value: str) -> str:
        if not 6 <= len(value) <= 20:
            raise ValueError("密码长度应不小于6且不大于20")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    email: str | None
    is_active: bool | None
