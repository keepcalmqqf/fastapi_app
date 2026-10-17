"""异步任务相关请求/响应模型（供任务控制台接口使用）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

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
