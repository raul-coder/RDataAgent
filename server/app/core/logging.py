"""结构化日志：JSON 输出 + trace_id 贯穿。"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from typing import Any, Optional

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": trace_id_var.get(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{time.strftime('%H:%M:%S')} {record.levelname:<7} "
            f"[{trace_id_var.get()}] {record.getMessage()}"
        )
        # 开发模式走文本格式（json_format = not DEBUG），若这里不输出 extra_fields，
        # 用 log_kv 传的结构化字段就全丢了 —— 而本地调试恰恰最需要看这些字段。
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict) and extra:
            rendered = " ".join(f"{k}={self._short(v)}" for k, v in extra.items())
            base += f"  | {rendered}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base

    @staticmethod
    def _short(v: Any, limit: int = 120) -> str:
        """长值（如完整 SQL）截断，避免单行日志刷屏。"""
        s = str(v)
        return s if len(s) <= limit else s[:limit] + f"…(+{len(s) - limit})"


def setup_logging(level: str = "INFO", json_format: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_format else TextFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # 压制第三方噪声
    for name in ("uvicorn.access", "httpx", "httpcore", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:16]
    trace_id_var.set(tid)
    return tid


def set_trace_id(tid: Optional[str]) -> str:
    trace_id_var.set(tid or "-")
    return trace_id_var.get()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_kv(
    logger: logging.Logger,
    level: int,
    msg: str,
    **fields: Any,
) -> None:
    """输出带结构化字段的日志。

    为什么需要它：直接写 `logger.debug(f"limit={s.limit}")` 只能得到一整串 msg，
    JSON 采集后无法按字段聚合过滤。改用 extra 传字典，`JsonFormatter`（:25）
    会把字段提升到顶层，日志系统里就能按 `slots.limit`、`qa.rows` 之类直接检索。

    文本模式下字段会退化为不显示（TextFormatter 只输出 msg），所以 msg 里
    仍需带上最关键的那一个值，避免 DEBUG 文本模式下看不到重点。

    字段值为 None 时自动剔除，减少噪音。
    """
    clean = {k: v for k, v in fields.items() if v is not None}
    logger.log(level, msg, extra={"extra_fields": clean} if clean else None)
