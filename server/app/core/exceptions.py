"""统一异常处理与业务错误码。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .logging import get_logger, trace_id_var

logger = get_logger(__name__)


class ErrorCode:
    OK = 0
    BAD_REQUEST = 40000
    UNAUTHORIZED = 40100
    TOKEN_EXPIRED = 40101
    FORBIDDEN = 40300
    NOT_FOUND = 40400
    CONFLICT = 40900
    RATE_LIMITED = 42900
    SQL_REJECTED = 43001       # SQL 未通过安全校验
    SQL_EXEC_ERROR = 43002     # SQL 执行失败
    LLM_ERROR = 43003          # 模型调用失败
    INTERNAL = 50000


class AppException(Exception):
    """业务异常基类。"""

    def __init__(
        self,
        message: str,
        code: int = ErrorCode.BAD_REQUEST,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: Any = None,
    ) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class UnauthorizedError(AppException):
    def __init__(self, message: str = "未登录或登录已过期") -> None:
        super().__init__(message, ErrorCode.UNAUTHORIZED, status.HTTP_401_UNAUTHORIZED)


class ForbiddenError(AppException):
    def __init__(self, message: str = "权限不足") -> None:
        super().__init__(message, ErrorCode.FORBIDDEN, status.HTTP_403_FORBIDDEN)


class NotFoundError(AppException):
    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(message, ErrorCode.NOT_FOUND, status.HTTP_404_NOT_FOUND)


class SQLRejectedError(AppException):
    """生成的 SQL 未通过安全校验，不重试。"""

    def __init__(self, message: str = "SQL 未通过安全校验", detail: Any = None) -> None:
        super().__init__(message, ErrorCode.SQL_REJECTED, status.HTTP_400_BAD_REQUEST, detail)


def _envelope(code: int, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data, "trace_id": trace_id_var.get()}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def _app_exc(_: Request, exc: AppException) -> JSONResponse:
        logger.warning("业务异常 code=%s msg=%s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope(ErrorCode.BAD_REQUEST, "参数校验失败", errors),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常：%s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(ErrorCode.INTERNAL, "服务内部错误"),
        )
