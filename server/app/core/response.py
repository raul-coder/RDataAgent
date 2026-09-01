"""统一响应包装。"""

from __future__ import annotations

from typing import Any, Optional

from .exceptions import ErrorCode
from .logging import trace_id_var


def ok(data: Any = None, message: str = "ok") -> dict:
    return {
        "code": ErrorCode.OK,
        "message": message,
        "data": data,
        "trace_id": trace_id_var.get(),
    }


def paged(items: list, total: int, page: int, page_size: int) -> dict:
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


def error(code: int, message: str, data: Optional[Any] = None) -> dict:
    return {"code": code, "message": message, "data": data, "trace_id": trace_id_var.get()}
