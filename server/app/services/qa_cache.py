"""问数结果缓存（T5-7）。

价值：
    演示与日常运营中，同一批问题会被反复问（"本月各单元收入排名"这类
    看板问题尤其如此）。命中缓存可以整段跳过 LLM，把 10s 级调用降到毫秒级，
    这是本项目把 P95 压下来的最有效手段——索引与视图优化对 LLM 延迟毫无帮助。

安全前提（务必注意）：
    缓存键**必须包含数据权限**。否则 A 用户问过的问题，会把结果串给
    权限范围不同的 B 用户——这是缓存最典型的越权坑，而且很难被发现。

失效：
    演示数据是静态的，因此按 TTL 过期即可；若接入了每日刷新的数据，
    应在数据装载后调用 clear() 清空。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Sequence

from ..core import redis
from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger(__name__)

PREFIX = "qa:cache:"


def build_key(
    question: str,
    source_ids: Optional[Sequence[int]],
    unit_codes: Optional[Sequence[str]],
) -> str:
    """缓存键：问题 + 数据源 + 数据权限。

    刻意**不含模型名**：真实模型来自数据库（模型路由是数据库优先），
    若每次问数都去解析一遍当前模型只为拼 key，等于给最快的路径加了一次查询。
    改为在 model_service 切换/修改模型时统一调用 clear() 失效。
    """
    payload = {
        "q": (question or "").strip(),
        "src": sorted(int(i) for i in (source_ids or [])),
        # 关键：权限范围不同 → 缓存键不同
        "units": sorted(str(u) for u in (unit_codes or [])),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get(key: str) -> Optional[Dict[str, Any]]:
    if settings.QA_CACHE_TTL <= 0 or redis.is_degraded():
        return None
    raw = redis.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("问数缓存解析失败，按未命中处理：%s", exc)
        return None


def put(key: str, value: Dict[str, Any]) -> None:
    if settings.QA_CACHE_TTL <= 0 or redis.is_degraded():
        return
    try:
        redis.setex(
            key, settings.QA_CACHE_TTL, json.dumps(value, ensure_ascii=False)
        )
    except Exception as exc:  # noqa: BLE001 - 缓存写失败不能影响问数主流程
        logger.warning("写入问数缓存失败：%s", exc)


def clear() -> int:
    """清空全部问数缓存（数据重新装载后调用）。"""
    return redis.delete_prefix(PREFIX)
