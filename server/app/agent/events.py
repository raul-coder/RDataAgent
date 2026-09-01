"""SSE 事件模型（问数全链路流式推送）。

事件序列：
    meta → step(1..5) → sql → table → chart → token* → followups → done
    异常时：error（可附带 retry 次数）
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# 5 步可解释链路（对齐 demo 的 renderAaSteps）
STEP_TITLES = (
    "选择数据表&数据时效",
    "推理逻辑",
    "执行取数SQL",
    "展示取数结果",
    "执行结束",
)

# 节点状态
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAIL = "fail"


@dataclass
class SSEEvent:
    event: str
    data: dict = field(default_factory=dict)

    def encode(self) -> str:
        """序列化为 SSE 帧。"""
        payload = json.dumps(self.data, ensure_ascii=False)
        return f"event: {self.event}\ndata: {payload}\n\n"


def meta_event(**kwargs: Any) -> SSEEvent:
    return SSEEvent("meta", kwargs)


def step_event(index: int, status: str, desc: str = "", cost_ms: int = 0) -> SSEEvent:
    return SSEEvent(
        "step",
        {
            "index": index,
            "title": STEP_TITLES[index - 1] if 1 <= index <= len(STEP_TITLES) else "",
            "status": status,
            "desc": desc,
            "cost_ms": cost_ms,
        },
    )


def sql_event(sql: str, data_sources: list[str]) -> SSEEvent:
    return SSEEvent("sql", {"sql": sql, "data_sources": data_sources})


def table_event(columns: list[str], rows: list[list], total: int, truncated: bool) -> SSEEvent:
    return SSEEvent(
        "table",
        {"columns": columns, "rows": rows, "total": total, "truncated": truncated},
    )


def chart_event(option: dict, chart_type: str) -> SSEEvent:
    return SSEEvent("chart", {"type": chart_type, "option": option})


def token_event(delta: str) -> SSEEvent:
    return SSEEvent("token", {"delta": delta})


def intent_event(intent: str, op: Optional[str] = None) -> SSEEvent:
    return SSEEvent("intent", {"intent": intent, "op": op})


def slots_event(text: str, slots: dict) -> SSEEvent:
    """当前生效的分析条件（多轮对话的上下文，前端可展示）"""
    return SSEEvent("slots", {"text": text, "slots": slots})


def clarify_event(question: str, options: list[str], reason: str) -> SSEEvent:
    """歧义时主动澄清反问，绝不臆测"""
    return SSEEvent("clarify", {"question": question, "options": options, "reason": reason})


def result_op_event(op: str, message: str) -> SSEEvent:
    return SSEEvent("result_op", {"op": op, "message": message})


def followups_event(items: list[str]) -> SSEEvent:
    return SSEEvent("followups", {"items": items})


def done_event(**kwargs: Any) -> SSEEvent:
    return SSEEvent("done", kwargs)


def error_event(code: str, message: str, retry: int = 0, detail: Any = None) -> SSEEvent:
    return SSEEvent(
        "error",
        {"code": code, "message": message, "retry": retry, "detail": detail},
    )
