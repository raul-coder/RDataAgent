"""结果二次加工：排序 / 改图 / 截取前 N / 导出。

这些操作**不重新执行 SQL** —— 直接对上一轮缓存的结果集做变换，
因此响应是毫秒级的（技术方案 §4.7 result_ops）。
"""

from __future__ import annotations

import io
import csv
import logging
import re
from typing import Any, Optional

from ...core.logging import get_logger, log_kv
from . import chart_advisor

logger = get_logger(__name__)

SORT_ASC = re.compile(r"升序|从小到大|最少|最低|正序")
CHART_KEYWORDS = (
    (re.compile(r"饼图|饼状|占比图|环形"), "pie"),
    (re.compile(r"折线|曲线|趋势图"), "line"),
    (re.compile(r"柱状|条形|柱图"), "bar"),
    (re.compile(r"表格|列表|明细"), "table"),
)
TOPN_PATTERNS = (
    re.compile(r"(?:只看|显示|取|保留)?前\s*(\d+)"),
    re.compile(r"TOP\s*(\d+)", re.I),
)


def _numeric_columns(columns: list[str], rows: list[list[Any]]) -> list[int]:
    out = []
    for j in range(len(columns)):
        vals = [r[j] for r in rows if j < len(r)]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums and len(nums) == len(vals) and vals:
            out.append(j)
    return out


def _match_column(question: str, columns: list[str]) -> Optional[int]:
    """从问题中匹配要排序的列名。"""
    for j, c in enumerate(columns):
        if c and c in question:
            return j
    return None


def apply(
    payload: dict,
    op: str,
    question: str,
) -> tuple[dict, str]:
    """对结果集施加操作，返回 (新结果, 说明文案)。"""
    columns: list[str] = list(payload.get("columns") or [])
    rows: list[list[Any]] = [list(r) for r in (payload.get("rows") or [])]

    log_kv(logger, logging.DEBUG, "结果二次加工开始", op=op, question=question,
           columns=columns, in_rows=len(rows))

    if not rows or not columns:
        log_kv(logger, logging.WARNING, "结果集为空，无法加工", op=op, question=question)
        return payload, "上一轮没有可操作的结果集，请重新提问。"

    if op == "sort":
        num_cols = _numeric_columns(columns, rows)
        idx = _match_column(question, columns)
        if idx is None or idx not in num_cols:
            idx = num_cols[-1] if num_cols else 0
        reverse = not bool(SORT_ASC.search(question))
        rows.sort(key=lambda r: _sort_key(r[idx]), reverse=reverse)
        direction = "降序" if reverse else "升序"
        new_payload = {**payload, "rows": rows}
        chart = chart_advisor.advise(columns, rows)
        return (
            {**new_payload, "chart": chart},
            f"已按「{columns[idx]}」{direction}重新排序，共 {len(rows)} 行。",
        )

    if op == "chart":
        target = None
        for pat, name in CHART_KEYWORDS:
            if pat.search(question):
                target = name
                break
        if target == "table":
            return {**payload, "chart": {"type": "table", "option": {}}}, "已切换为表格展示。"
        chart = chart_advisor.advise(
            columns, rows, hint={"type": target or "auto"}
        )
        return {**payload, "chart": chart}, f"已切换为{_chart_label(chart['type'])}展示。"

    if op == "topn":
        n = None
        for pat in TOPN_PATTERNS:
            m = pat.search(question)
            if m:
                n = int(m.group(1))
                break
        if n is None or n <= 0:
            n = 10
        kept = rows[: min(n, len(rows))]
        return {**payload, "rows": kept, "total": len(kept)}, f"已截取前 {len(kept)} 行。"

    if op == "export":
        # 导出由前端用当前结果集生成文件，这里只回传标识
        return payload, f"共 {len(rows)} 行数据，可在结果区右上角导出。"

    return payload, "未能识别的操作。"


def _sort_key(v: Any) -> tuple[int, float]:
    if isinstance(v, bool):
        return (0, int(v))
    if isinstance(v, (int, float)):
        return (0, float(v))
    try:
        return (0, float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return (1, 0.0)


def _chart_label(t: str) -> str:
    return {"pie": "饼图", "line": "折线图", "bar": "柱状图", "metric": "指标卡", "table": "表格"}.get(t, t)


def to_csv(columns: list[str], rows: list[list[Any]]) -> str:
    """结果集 → CSV 文本（UTF-8 BOM，Excel 可直接打开）。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return "\ufeff" + buf.getvalue()
