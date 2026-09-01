"""运行看板（T5-9）：问数成功率、耗时分位、缓存命中率。

关于技术选型的说明：
    需求原文提到「OTel 埋点 + Grafana 看板 + 告警规则」。对单机演示而言，
    引入 Prometheus + Grafana 意味着多套中间件要维护，而要看的指标
    （成功率、耗时、缓存命中）**库里本来就有的**——chat_message 记录了
    每轮问数的 cost_ms 与 error，payload 里还有 cached 标记。
    因此这里直接用 SQL 统计，零额外依赖；若日后接入了 OTel，
    本接口的口径可以平移过去，前端无需改动。

数据范围：
    管理员（有 sys:log:view）看全量，普通用户只看自己的——
    运行指标也属于数据，不该让普通用户看到全公司的问数情况。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, get_current_user
from ...core.response import ok
from ...db.session import get_db

router = APIRouter(prefix="/stats", tags=["stats"])

# 只统计助手消息：用户消息没有耗时
_BASE_SQL = """
    SELECT
        COUNT(*)                                                       AS total,
        COUNT(*) FILTER (WHERE error IS NULL OR error = '')             AS ok,
        COALESCE(PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY cost_ms), 0) AS p50,
        COALESCE(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cost_ms), 0) AS p95,
        COALESCE(AVG(cost_ms), 0)                                       AS avg_ms,
        COUNT(*) FILTER (WHERE payload->>'cached' = 'true')             AS cached
    FROM chat_message m
    WHERE m.role = 'assistant'
      AND m.cost_ms IS NOT NULL
      AND m.cost_ms > 0
      AND m.created_at >= :since
"""


@router.get("/qa", summary="问数运行看板")
async def qa_stats(
    days: int = Query(1, ge=1, le=30, description="统计最近天数"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    params: dict[str, Any] = {"since": since}

    sql = _BASE_SQL
    if not (user.is_superadmin or user.has("sys:log:view")):
        sql += " AND m.session_id IN (SELECT id FROM chat_session WHERE user_id = :uid)"
        params["uid"] = user.id

    row = (await db.execute(text(sql), params)).mappings().first() or {}

    total = int(row.get("total") or 0)
    ok_cnt = int(row.get("ok") or 0)
    cached = int(row.get("cached") or 0)

    # 按天看趋势，便于判断"是不是刚刚才变差"
    trend_sql = """
        SELECT to_char(m.created_at, 'YYYY-MM-DD') AS d,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE error IS NULL OR error = '') AS ok,
               COALESCE(AVG(m.cost_ms), 0) AS avg_ms
        FROM chat_message m
        WHERE m.role = 'assistant' AND m.cost_ms > 0 AND m.created_at >= :since
    """
    if not (user.is_superadmin or user.has("sys:log:view")):
        trend_sql += " AND m.session_id IN (SELECT id FROM chat_session WHERE user_id = :uid)"
    trend_sql += " GROUP BY d ORDER BY d"

    trend_rows = (await db.execute(text(trend_sql), params)).mappings().all()

    return ok({
        "days": days,
        "total": total,
        "ok": ok_cnt,
        "failed": total - ok_cnt,
        "success_rate": round(ok_cnt / total * 100, 1) if total else 0.0,
        "p50_ms": int(row.get("p50") or 0),
        "p95_ms": int(row.get("p95") or 0),
        "avg_ms": int(row.get("avg_ms") or 0),
        "cached": cached,
        "cache_hit_rate": round(cached / total * 100, 1) if total else 0.0,
        "trend": [
            {
                "date": r["d"],
                "total": int(r["total"]),
                "ok": int(r["ok"]),
                "avg_ms": int(r["avg_ms"] or 0),
            }
            for r in trend_rows
        ],
    })
