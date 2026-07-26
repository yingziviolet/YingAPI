"""trace_id 全链路:中间件生成/透传 X-Trace-Id,contextvar 供日志与计量使用。"""
import re
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

_VALID_TRACE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace_id() -> str:
    tid = trace_id_var.get()
    if not tid:
        tid = new_trace_id()
        trace_id_var.set(tid)
    return tid


class TraceIdMiddleware(BaseHTTPMiddleware):
    """接受调用方传入的 X-Trace-Id(合法格式才采纳),否则生成新的;响应头回传。"""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-trace-id", "")
        tid = incoming if _VALID_TRACE.match(incoming) else new_trace_id()
        trace_id_var.set(tid)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = tid
        return response
