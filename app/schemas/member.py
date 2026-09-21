from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterMember(BaseModel):
    nickname: str = Field(min_length=2, max_length=10)
    email: EmailStr
    password: str = Field(min_length=6, max_length=20)


class MemberLogin(BaseModel):
    email: EmailStr
    password: str


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nickname: str | None
    email: str | None
    is_active: bool
