"""异步任务相关请求/响应模型（供任务控制台接口使用）。"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 单次投递允许的最大重试次数：再大只会让失败任务反复占位，观测面也更难读懂
_MAX_RETRIES_LIMIT = 10


class TaskDispatchIn(BaseModel):
    """手动投递一个具名任务的请求体。

    参数须可 JSON 序列化（redis 后端要写进 Stream，memory 后端也按此约束保持一致）。
    """

    name: str = Field(min_length=1, max_length=64, description="具名任务名（须已注册）")
    args: list = Field(default_factory=list, description="位置参数，JSON 数组")
    kwargs: dict = Field(default_factory=dict, description="关键字参数，JSON 对象")
    retries: int = Field(default=0, ge=0, le=_MAX_RETRIES_LIMIT, description="失败自动重试次数")


class TaskDispatchOut(BaseModel):
    """投递结果：任务名 + job_id（后续用它查状态）。"""

    name: str
    job_id: str


class TaskHistoryOut(BaseModel):
    """执行历史条目，对应 ORM 模型 TaskRun（app/models/task.py）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: str
    name: str
    backend: str
    state: str
    attempt: int
    retries: int
    enqueued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    result: str | None


class TaskHistoryQuery(BaseModel):
    """执行历史查询条件：全部可选，配合分页参数使用（时间语义同 AuditLogQuery）。"""

    name: str | None = Field(default=None, max_length=64)
    state: str | None = Field(default=None, max_length=16)
    enqueued_at_from: datetime | None = None
    enqueued_at_to: datetime | None = None

    @field_validator("enqueued_at_from", "enqueued_at_to", mode="after")
    @classmethod
    def _normalize_to_utc(cls, value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @model_validator(mode="after")
    def _check_range(self) -> TaskHistoryQuery:
        if (
            self.enqueued_at_from is not None
            and self.enqueued_at_to is not None
            and self.enqueued_at_from > self.enqueued_at_to
        ):
            raise ValueError("enqueued_at_from 不能晚于 enqueued_at_to")
        return self
