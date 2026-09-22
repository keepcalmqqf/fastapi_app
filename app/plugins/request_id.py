import contextvars
import logging
import re
import uuid

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# 只接受字母数字/连字符/下划线、长度不超过 64 的请求 ID，防止注入恶意值
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("X-Request-ID")
        if not rid or not _REQUEST_ID_RE.fullmatch(rid):
            rid = uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


def setup(app: FastAPI) -> None:
    root = logging.getLogger()
    log_filter = RequestIdFilter()
    for handler in root.handlers:
        handler.addFilter(log_filter)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
        )
    app.add_middleware(RequestIdMiddleware)
