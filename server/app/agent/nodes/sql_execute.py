"""SQL 执行节点：只读连接 + 超时 + 行数上限 + 结果缓存。"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.logging import get_logger, log_kv

logger = get_logger(__name__)


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    total: int = 0
    truncated: bool = False
    cost_ms: int = 0
    cached: bool = False


def _normalize(sql: str) -> str:
    return " ".join((sql or "").split()).lower()


def cache_key(sql: str) -> str:
    return hashlib.md5(_normalize(sql).encode("utf-8")).hexdigest()


def _convert(value: Any) -> Any:
    """把数据库类型转成可 JSON 序列化的值。"""
    if value is None:
        return None
    if isinstance(value, (int, bool)):
        return value
    if isinstance(value, float):
        return round(value, 4)
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return float(round(value, 4))
    except Exception:  # noqa: BLE001
        pass
    return str(value)


async def execute_sql(
    session: AsyncSession, sql: str, *, use_cache: bool = True
) -> QueryResult:
    """在只读连接上执行查询。"""
    from ...core import redis

    key = f"sqlcache:{cache_key(sql)}"
    if use_cache and not redis.is_degraded():
        cached = redis.get(key)
        if cached:
            try:
                payload = json.loads(cached)
                log_kv(logger, logging.DEBUG, "SQL 结果缓存命中", key=key, rows=payload.get("total"),
                       sql=sql[:300])
                return QueryResult(cached=True, **payload)
            except Exception:  # noqa: BLE001
                pass

    import time

    t0 = time.perf_counter()

    # 会话级只读与超时兜底（连接账号本身也是只读的）
    try:
        await session.execute(text(f"SET LOCAL statement_timeout = {int(settings.SQL_TIMEOUT_MS)}"))
        await session.execute(text("SET LOCAL default_transaction_read_only = on"))
        cursor = await session.execute(text(sql))
        columns = list(cursor.keys())
        raw_rows = cursor.fetchall()
    except Exception:
        # 关键：PostgreSQL 中一条语句失败会中止整个事务，若不回滚，
        # 自愈重试时使用同一会话会直接抛 InFailedSQLTransactionError。
        try:
            await session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.warning("执行失败后回滚出错：%s", rb_exc)
        raise

    cap = settings.SQL_MAX_ROWS
    truncated = len(raw_rows) > cap
    if truncated:
        # 截断会让「前端看到的合计」小于「数据库真实合计」，
        # 是「两个数字对不上」的高频原因之一，必须显式告警而不是静默切一刀。
        log_kv(logger, logging.WARNING, "SQL 结果被截断", returned=cap,
               actual=len(raw_rows), cap=cap, sql=sql[:300])
    raw_rows = raw_rows[:cap]

    rows = [[_convert(v) for v in row] for row in raw_rows]
    cost_ms = int((time.perf_counter() - t0) * 1000)

    result = QueryResult(
        columns=columns,
        rows=rows,
        total=len(rows),
        truncated=truncated,
        cost_ms=cost_ms,
    )

    if use_cache and not redis.is_degraded():
        try:
            redis.setex(key, 300, json.dumps({
                "columns": result.columns,
                "rows": result.rows,
                "total": result.total,
                "truncated": result.truncated,
                "cost_ms": result.cost_ms,
            }, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass

    logger.info("SQL 执行完成：%d 行 / %dms", result.total, cost_ms)
    return result


def summarize(result: QueryResult, max_rows: int = 200) -> dict:
    """程序化计算统计摘要，供结论生成使用（避免模型心算出错）。"""
    numeric_cols: dict[int, list[float]] = {}
    for i, row in enumerate(result.rows):
        for j, v in enumerate(row):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                numeric_cols.setdefault(j, []).append(float(v))

    stats: dict[str, Any] = {
        "row_count": result.total,
        "truncated": result.truncated,
        "columns": result.columns,
    }

    single = len(result.rows) == 1

    if numeric_cols:
        # 取「非序号」列中数值跨度最大的两列作为主指标
        def span(j: int) -> float:
            vals = numeric_cols[j]
            return (max(vals) - min(vals)) if vals else 0.0

        main_cols = sorted(numeric_cols.keys(), key=span, reverse=True)[:2]
        for j in main_cols:
            vals = numeric_cols[j]
            col = result.columns[j] if j < len(result.columns) else str(j)
            item: dict[str, Any] = {"sum": round(sum(vals), 2)}
            # 只有一行时 max/min/avg 与 sum 完全相同，纯属噪声，会让结论显得啰嗦
            if not single:
                item.update({
                    "max": round(max(vals), 2),
                    "min": round(min(vals), 2),
                    "avg": round(sum(vals) / len(vals), 2),
                })
            stats[col] = item

    stats["preview"] = result.rows[:max_rows]
    return stats


def preview_payload(result: QueryResult, limit: int = 200) -> dict:
    return {
        "columns": result.columns,
        "rows": result.rows[:limit],
        "total": result.total,
        "truncated": result.truncated,
    }
