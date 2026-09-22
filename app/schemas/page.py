from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageParams(BaseModel):
    """分页参数依赖：page 从 1 开始，page_size 上限 100。"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class Page(BaseModel, Generic[T]):
    """统一分页返回结构：total 为符合条件的总记录数，items 为当前页数据。"""

    total: int
    page: int
    page_size: int
    items: list[T]
