from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _to_utc_naive(value: datetime | None) -> datetime | None:
    """把入参时间统一成 UTC 朴素时间，与库内 created_at 对齐。

    时间列由应用侧以 UTC 写入（见 app/models/base.utc_now），MySQL DATETIME 不带时区，
    所以比较前必须先把带时区偏移的入参换算到 UTC 再去掉 tzinfo，否则东八区入参会
    比实际早/晚 8 小时；无偏移的入参按 UTC 解释。
    """
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


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
    """审计日志查询条件：全部可选，配合分页参数使用。

    时间范围建议传带时区的 ISO 8601（如 2026-10-01T00:00:00+08:00 或 ...Z），
    服务端会换算到 UTC 后比较；不传偏移量则按 UTC 解释。
    """

    action: str | None = Field(default=None, max_length=64)
    actor_id: int | None = None
    # 闭区间：[created_at_from, created_at_to]
    created_at_from: datetime | None = None
    created_at_to: datetime | None = None

    @field_validator("created_at_from", "created_at_to", mode="after")
    @classmethod
    def _normalize_to_utc(cls, value: datetime | None) -> datetime | None:
        return _to_utc_naive(value)

    @model_validator(mode="after")
    def _check_range(self) -> "AuditLogQuery":
        if (
            self.created_at_from is not None
            and self.created_at_to is not None
            and self.created_at_from > self.created_at_to
        ):
            raise ValueError("created_at_from 不能晚于 created_at_to")
        return self
