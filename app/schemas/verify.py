from pydantic import BaseModel, Field


class VerifyEmailIn(BaseModel):
    """邮箱验证入参：来自验证邮件链接中的 token。"""

    token: str = Field(min_length=1, max_length=2048)
