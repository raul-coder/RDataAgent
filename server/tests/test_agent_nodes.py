"""Agent 节点单测（不依赖数据库与模型）。"""

from __future__ import annotations

import pytest

from app.agent.nodes import chart_advisor
from app.agent.nodes.compose import fallback_answer, suggest_followups
from app.agent.nodes.sql_execute import summarize
from app.agent.nodes.sql_validate import looks_like_ranking, validate
from app.core.exceptions import SQLRejectedError


def _result(columns, rows):
    class R:
        total = len(rows)
        truncated = False
        cost_ms = 1

    r = R()
    r.columns = columns
    r.rows = rows
    return r


# ── 图表推荐 ────────────────────────────────────────────────────────
def test_metric_card_for_single_value():
    out = chart_advisor.advise(["高风险项目金额"], [[14116.2]])
    assert out["type"] == "metric"
    assert out["option"]["value"] == 14116.2


def test_pie_for_proportion():
    # 饼图分支曾引用未定义变量 s，这里专门覆盖
    out = chart_advisor.advise(
        ["产品线", "收入", "占比"],
        [["通用计算", 100.0, 55.5], ["智能计算", 60.0, 33.3], ["商业解决方案", 20.0, 11.1]],
    )
    assert out["type"] == "pie"
    assert len(out["option"]["series"][0]["data"]) == 3
    assert out["option"]["series"][0]["data"][0]["name"] == "通用计算"


def test_line_for_time_series():
    rows = [[str(m), float(m * 100)] for m in range(1, 13)]
    out = chart_advisor.advise(["月份", "合同金额"], rows)
    assert out["type"] == "line"
    assert len(out["option"]["xAxis"]["data"]) == 12


def test_bar_for_ranking():
    rows = [[f"单元{i}", 1000.0 - i] for i in range(5)]
    out = chart_advisor.advise(["经营单元", "商业收入"], rows)
    assert out["type"] == "bar"


# ── 结论兜底 ────────────────────────────────────────────────────────
def test_fallback_answer_with_rows():
    stats = summarize(_result(["商业收入"], [[100.0], [200.0]]))
    text = fallback_answer("测试", ["商业收入"], [[100.0], [200.0]], stats)
    assert "2 行" in text
    assert "200" in text


def test_fallback_answer_empty():
    stats = summarize(_result(["x"], []))
    text = fallback_answer("测试", ["x"], [], stats)
    assert "没有查询到数据" in text


def test_single_row_stats_has_no_redundant_extremes():
    stats = summarize(_result(["金额"], [[42.0]]))
    assert "max" not in stats["金额"]  # 单行时极值等于合计，属于噪声


def test_suggest_followups():
    items = suggest_followups("2026年高风险项目有哪些", ["合同编号"])
    assert len(items) == 3
    assert "同比情况如何？" in items


# ── SQL 校验（含维表白名单）────────────────────────────────────────
ALLOWED = [
    "bi.fact_contract", "bi.fact_goal", "bi.dim_unit", "bi.dim_industry",
    "bi.dim_product", "bi.dim_sales", "bi.dim_customer",
]


def test_validate_accepts_dimension_join():
    sql = ("SELECT d.unit_name, SUM(f.year_income) FROM bi.fact_contract AS f "
           "LEFT JOIN bi.dim_unit AS d ON d.unit_code = f.unit_code "
           "WHERE f.year = 2026 GROUP BY d.unit_name ORDER BY 2 DESC LIMIT 10")
    out = validate(sql, ALLOWED, ["SH", "ZJ"])
    assert "unit_code" in out
    assert "'SH'" in out


def test_validate_injects_limit():
    out = validate("SELECT unit_code FROM bi.fact_contract", ALLOWED, None)
    assert "LIMIT" in out.upper()


def test_validate_allows_cte_alias():
    """CTE 是虚拟表，不能按物理表白名单拦截。"""
    sql = (
        "WITH goal_agg AS (SELECT unit_code, SUM(biz_goal) AS biz_goal "
        "FROM bi.fact_goal WHERE year = 2026 GROUP BY unit_code) "
        "SELECT g.biz_goal FROM goal_agg g WHERE g.biz_goal > 0"
    )
    out = validate(sql, ALLOWED, None)
    assert "goal_agg" in out
    assert "LIMIT" in out.upper()


def test_validate_rejects_unknown_table():
    with pytest.raises(SQLRejectedError):
        validate("SELECT * FROM bi.secret_table", ALLOWED, None)


def test_validate_rejects_write():
    with pytest.raises(SQLRejectedError):
        validate("DELETE FROM bi.fact_contract", ALLOWED, None)


def test_looks_like_ranking():
    assert looks_like_ranking("各经营单元收入排名")
    assert looks_like_ranking("销售最多的3个产品型号")
    assert not looks_like_ranking("各产品线收入占比")
