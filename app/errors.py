"""OpenAI 兼容错误格式:数据面所有错误统一 {"error": {...}} 结构。"""
from fastapi.responses import JSONResponse


def openai_error(
    status_code: int, message: str, err_type: str = "invalid_request_error", code: str | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": err_type, "code": code}},
    )


class UpstreamError(Exception):
    """上游最终失败(所有渠道尝试完毕或不可 failover 的客户端错误)。"""

    def __init__(self, status_code: int, message: str, body: dict | None = None):
        self.status_code = status_code
        self.message = message
        self.body = body  # 上游原始错误体(可透传)
        super().__init__(message)
