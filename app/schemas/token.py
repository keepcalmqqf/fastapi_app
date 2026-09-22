from pydantic import BaseModel


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # access token 有效期（秒）
    expires_in: int
