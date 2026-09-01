"""图表推荐：确定性规则（不额外消耗模型调用）。

优先级：
    单值 → 指标卡
    时间维度 ≥3 点 → 折线图
    占比列（列名含「占比」或百分比）→ 饼图
    排名类（≤10 行且有数值序）→ 横向条形图
    双数值列 → 分组柱状图
    其它 → 表格
"""

from __future__ import annotations

from typing import Any

TIME_HINTS = ("月份", "月", "季度", "年份", "年", "日期", "year_month", "week")
PCT_HINTS = ("占比", "比例", "完成率", "达成率", "率")


def advise(
    columns: list[str],
    rows: list[list[Any]],
    *,
    hint: dict | None = None,
) -> dict:
    """返回 {type, option} —— option 为 ECharts 配置。"""
    hint = hint or {}
    if not rows or not columns:
        return _metric_card(columns, rows)

    # 单值一律用指标卡（模型可能会误判为 bar，画出来毫无意义）
    if len(rows) == 1 and len(columns) == 1:
        return _metric_card(columns, rows)

    # 模型显式指定且合法
    declared = str(hint.get("type") or "").lower()
    if declared in {"bar", "line", "pie", "table", "metric"} and declared != "auto":
        return _build(declared, columns, rows, hint)

    # 找出维度列（非数值）与数值列
    cat_idx: list[int] = []
    num_idx: list[int] = []
    for j in range(len(columns)):
        vals = [r[j] for r in rows if j < len(r)]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums and len(nums) == len(vals) and len(vals) > 0:
            num_idx.append(j)
        else:
            cat_idx.append(j)

    if not cat_idx or not num_idx:
        return {"type": "table", "option": {}}

    cat = cat_idx[0]

    # 时间趋势
    if any(h in columns[cat] for h in TIME_HINTS) and len(rows) >= 3:
        return _build("line", columns, rows, {"x": columns[cat], "y": columns[num_idx[0]]})

    # 占比
    if any(h in columns[j] for j in num_idx for h in PCT_HINTS) or any(
        h in columns[j] for j in num_idx for h in PCT_HINTS
    ):
        pct_col = next((j for j in num_idx if any(h in columns[j] for h in PCT_HINTS)), num_idx[0])
        return _build("pie", columns, rows, {"x": columns[cat], "y": columns[pct_col]})

    # 排名（行数少 → 横向条形）
    if len(rows) <= 12:
        return _build("bar", columns, rows, {"x": columns[cat], "y": columns[num_idx[0]], "horizontal": True})

    # 双数值 → 分组柱状
    if len(num_idx) >= 2:
        return _build("bar", columns, rows, {
            "x": columns[cat],
            "y": [columns[j] for j in num_idx[:2]],
        })

    return _build("bar", columns, rows, {"x": columns[cat], "y": columns[num_idx[0]]})


def _build(chart_type: str, columns: list[str], rows: list[list[Any]], hint: dict) -> dict:
    x_name = hint.get("x") or (columns[0] if columns else "")
    y_names = hint.get("y")
    y_list = [y_names] if isinstance(y_names, str) else (y_names or _auto_y(columns, rows))

    x_idx = columns.index(x_name) if x_name in columns else 0
    categories = [_fmt(r[x_idx]) for r in rows if x_idx < len(r)]

    y_idx_list = [columns.index(y) for y in y_list if y in columns] or _auto_y_idx(columns, rows)

    series = []
    for j in y_idx_list:
        series.append({
            "name": columns[j] if j < len(columns) else f"col{j}",
            "data": [_num(r[j]) for r in rows if j < len(r)],
        })

    horizontal = bool(hint.get("horizontal"))

    if chart_type == "pie":
        # 饼图只取第一个数值序列
        first = series[0]["data"] if series else []
        option = {
            "tooltip": {"trigger": "item", "valueFormatter": None},
            "legend": {"bottom": 0},
            "series": [{
                "type": "pie",
                "radius": ["42%", "68%"],
                "data": [
                    {"name": c, "value": first[i] if i < len(first) else 0}
                    for i, c in enumerate(categories)
                ],
            }],
        }
        return {"type": "pie", "option": option}

    if chart_type == "line":
        option = {
            "tooltip": {"trigger": "axis"},
            "legend": {"bottom": 0},
            "grid": {"left": 48, "right": 24, "top": 32, "bottom": 48},
            "xAxis": {"type": "category", "data": categories},
            "yAxis": {"type": "value"},
            "series": [{"name": s["name"], "type": "line", "smooth": True, "data": s["data"]} for s in series],
        }
        return {"type": "line", "option": option}

    option = {
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"bottom": 0},
        "grid": {"left": 48, "right": 24, "top": 32, "bottom": 48},
        "xAxis": {"type": "value" if horizontal else "category", "data": None if horizontal else categories},
        "yAxis": {"type": "category" if horizontal else "value", "data": categories if horizontal else None},
        "series": [{"name": s["name"], "type": "bar", "data": s["data"]} for s in series],
    }
    return {"type": "bar", "option": option}


def _metric_card(columns: list[str], rows: list[list[Any]]) -> dict:
    value = rows[0][0] if rows and rows[0] else None
    return {
        "type": "metric",
        "option": {"label": columns[0] if columns else "结果", "value": value},
    }


def _auto_y(columns: list[str], rows: list[list[Any]]) -> list[str]:
    idx = _auto_y_idx(columns, rows)
    return [columns[i] for i in idx if i < len(columns)]


def _auto_y_idx(columns: list[str], rows: list[list[Any]]) -> list[int]:
    out: list[int] = []
    for j in range(len(columns)):
        vals = [r[j] for r in rows if j < len(r)]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums and len(nums) == len(vals) and vals:
            out.append(j)
        if len(out) >= 2:
            break
    return out or [len(columns) - 1] if columns else []


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return str(v)


def _num(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0
