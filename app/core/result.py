from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    code: int = 200
    data: T | None = None
    message: str = "请求成功"


def ok(data: T | None = None, message: str = "请求成功", code: int = 200) -> Result[T]:
    return Result(code=code, data=data, message=message)


def failure(code: int = 500, message: str = "请求失败") -> Result[None]:
    return Result(code=code, data=None, message=message)
