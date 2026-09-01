"""会话上下文：槽位状态 + 上轮结果引用 + 长对话摘要。

存 Redis（TTL 2 小时）；Redis 不可用时退化为请求内内存，功能不中断。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..core import redis
from ..core.logging import get_logger
from .slots import Slots

logger = get_logger(__name__)

TTL = 7200  # 2 小时
SUMMARY_AFTER_TURNS = 6  # 超过 6 轮开始压缩历史


@dataclass
class SessionContext:
    session_id: int
    active_slots: Slots = field(default_factory=Slots)
    last_result_key: Optional[str] = None   # 上轮结果集缓存 key（结果二次加工用）
    last_sql: str = ""
    turn_count: int = 0
    summary: str = ""                        # 长对话压缩后的摘要
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "active_slots": self.active_slots.to_dict(),
            "last_result_key": self.last_result_key,
            "last_sql": self.last_sql,
            "turn_count": self.turn_count,
            "summary": self.summary,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionContext":
        return cls(
            session_id=int(d.get("session_id", 0)),
            active_slots=Slots.from_dict(d.get("active_slots")),
            last_result_key=d.get("last_result_key"),
            last_sql=d.get("last_sql") or "",
            turn_count=int(d.get("turn_count") or 0),
            summary=d.get("summary") or "",
            updated_at=float(d.get("updated_at") or time.time()),
        )


def _key(session_id: int) -> str:
    return f"ctx:{session_id}"


def load(session_id: int) -> SessionContext:
    raw = redis.get(_key(session_id))
    if raw:
        try:
            return SessionContext.from_dict(json.loads(raw))
        except Exception:  # noqa: BLE001
            logger.warning("会话上下文解析失败，重建 session=%s", session_id)
    return SessionContext(session_id=session_id)


def save(ctx: SessionContext) -> None:
    ctx.updated_at = time.time()
    redis.setex(_key(ctx.session_id), TTL, json.dumps(ctx.to_dict(), ensure_ascii=False))


def clear(session_id: int) -> None:
    redis.delete(_key(session_id))


# ── 结果集缓存（供「结果二次加工」使用）──────────────────────────
def cache_result(session_id: int, payload: dict) -> str:
    """缓存上轮结果集，返回 key。"""
    key = f"rs:{session_id}:{int(time.time() * 1000)}"
    redis.setex(key, TTL, json.dumps(payload, ensure_ascii=False))
    return key


def load_result(key: str) -> Optional[dict]:
    raw = redis.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def needs_summary(ctx: SessionContext) -> bool:
    return ctx.turn_count > SUMMARY_AFTER_TURNS
