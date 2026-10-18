from typing import Generic, TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")


# 最大可翻取的偏移量（page × page_size）。MySQL 的 OFFSET 是真跳读：翻到第 N 页
# 要先扫过前 N-1 页再丢弃，偏移量越大单次查询越接近全表扫描。
# 此前 page 上限 10000 × page_size 100 = 偏移 100 万，配合 COUNT 全表统计，
# 单个匿名请求就能把库拖住。需要更深的分页请改用 id 游标（本脚手架未实现）。
_MAX_OFFSET = 100_000


class PageParams(BaseModel):
    """分页参数依赖：page 从 1 开始，page_size 上限 100，两者乘积另有上限。

    page 与 page_size 都设上限，且乘积不得超过 _MAX_OFFSET：只限制单侧时
    ?page=10000&page_size=100 依然能构造出百万级偏移。超过上限直接 422，
    而不是让数据库去执行一次注定很慢的查询。
    """

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def _check_offset(self) -> "PageParams":
        if self.page * self.page_size > _MAX_OFFSET + self.page_size:
            raise ValueError(f"分页过深：偏移量上限为 {_MAX_OFFSET}，请缩小页码或改用游标分页")
        return self


class Page(BaseModel, Generic[T]):
    """统一分页返回结构：total 为符合条件的总记录数，items 为当前页数据。"""

    total: int
    page: int
    page_size: int
    items: list[T]
