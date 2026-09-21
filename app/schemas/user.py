from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CreateUser(BaseModel):
    name: str = Field(min_length=2, max_length=10)
    email: EmailStr
    password: str = Field(min_length=6, max_length=20)
    is_active: bool = True


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    email: str | None
    is_active: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
