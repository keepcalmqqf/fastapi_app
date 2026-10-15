from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_type: str
    actor_id: int | None
    action: str
    target_type: str
    target_id: str | None
    detail: str | None
    ip: str | None
    created_at: datetime


class AuditLogQuery(BaseModel):
    """审计日志查询条件：全部可选，配合分页参数使用。"""

    action: str | None = Field(default=None, max_length=64)
    actor_id: int | None = None
