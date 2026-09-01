"""数据权限注入测试（安全关键，覆盖 5 类 SQL 形态）。"""

from __future__ import annotations

import pytest

from app.core.exceptions import SQLRejectedError
from app.security.sql_guard import apply_data_permission, assert_readonly

UNITS = ["SH", "ZJ"]


def test_simple_where():
    """形态 1：普通带 WHERE 的查询 → 追加 AND 条件。"""
    sql = "SELECT d.unit_name, SUM(f.year_income) AS income FROM bi.fact_contract f JOIN bi.dim_unit d ON d.unit_code = f.unit_code WHERE f.year = 2026 GROUP BY d.unit_name"
    out = apply_data_permission(sql, UNITS)
    assert "AND" in out.upper()
    assert "unit_code" in out
    assert "'SH'" in out and "'ZJ'" in out
    assert "f.year = 2026" in out  # 原条件保留


def test_without_where():
    """形态 2：无 WHERE 的聚合 → 新建 WHERE。"""
    sql = "SELECT unit_code, SUM(year_income) AS income FROM bi.fact_contract GROUP BY unit_code"
    out = apply_data_permission(sql, UNITS)
    assert "WHERE" in out.upper()
    assert "'SH'" in out


def test_subquery_injects_inner():
    """形态 3：外层包裹子查询 → 只注入最内层，不重复注入。"""
    sql = (
        "SELECT * FROM (SELECT d.unit_name AS 经营单元, SUM(f.year_income) AS 收入 "
        "FROM bi.fact_contract f JOIN bi.dim_unit d ON d.unit_code = f.unit_code "
        "WHERE f.year = 2026 GROUP BY d.unit_name) t ORDER BY 收入 DESC"
    )
    out = apply_data_permission(sql, UNITS)
    # 只出现一次 IN 条件（内层）
    assert out.upper().count(" IN ") == 1
    assert "'SH'" in out


def test_cte_injects_cte_body():
    """形态 4：CTE → 注入 CTE 内部。"""
    sql = (
        "WITH agg AS (SELECT unit_code, SUM(year_income) AS income FROM bi.fact_contract "
        "GROUP BY unit_code) SELECT * FROM agg ORDER BY income DESC"
    )
    out = apply_data_permission(sql, UNITS)
    assert out.upper().startswith("WITH")
    assert out.upper().count(" IN ") == 1
    assert "'ZJ'" in out


def test_view_source():
    """形态 5：查询报表视图 → 对视图别名注入。"""
    sql = "SELECT unit_name, achieve_rate FROM bi.v_overall_achieve WHERE year = 2026"
    out = apply_data_permission(sql, UNITS)
    assert "'SH'" in out
    assert "year = 2026" in out


def test_no_restriction_returns_original():
    """不限制（None / 空）时原样返回。"""
    sql = "SELECT unit_code FROM bi.fact_contract"
    assert apply_data_permission(sql, None) == sql
    assert apply_data_permission(sql, []) == sql


def test_non_unit_query_untouched():
    """与经营单元无关的查询（仅维度表）不应被改写。"""
    sql = "SELECT unit_code, unit_name FROM bi.dim_unit"
    assert apply_data_permission(sql, UNITS) == sql


def test_union_both_branches():
    """UNION 两侧都需要注入。"""
    sql = (
        "SELECT unit_code FROM bi.fact_contract WHERE year = 2025 "
        "UNION ALL SELECT unit_code FROM bi.fact_contract WHERE year = 2026"
    )
    out = apply_data_permission(sql, UNITS)
    assert out.upper().count(" IN ") == 2


def test_inject_into_exists_subquery():
    """EXISTS 子查询中的受限表同样要被过滤（纵深防御）。"""
    sql = "SELECT * FROM (SELECT 1 AS x) t WHERE EXISTS (SELECT 1 FROM bi.fact_contract)"
    out = apply_data_permission(sql, UNITS)
    assert out.upper().count(" IN ") == 1
    assert "'SH'" in out


def test_reject_when_no_injection_point(monkeypatch):
    """定位不到注入点时必须拒绝，绝不能放行越权查询。"""
    import app.security.sql_guard as guard

    monkeypatch.setattr(guard, "_leaf_unit_selects", lambda tree: [])
    sql = "SELECT unit_code, SUM(year_income) FROM bi.fact_contract GROUP BY unit_code"
    with pytest.raises(SQLRejectedError):
        guard.apply_data_permission(sql, UNITS)


def test_reject_invalid_sql():
    with pytest.raises(SQLRejectedError):
        apply_data_permission("SELECT * FROM WHERE ???", UNITS)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM bi.fact_contract",
        "DROP TABLE bi.fact_contract",
        "UPDATE bi.fact_contract SET amount = 0",
        "INSERT INTO bi.fact_contract (id) VALUES (1)",
    ],
)
def test_assert_readonly_rejects(sql):
    with pytest.raises(SQLRejectedError):
        assert_readonly(sql)


def test_assert_readonly_allows_select():
    assert_readonly("SELECT * FROM bi.fact_contract WHERE year = 2026")
    assert_readonly("WITH a AS (SELECT 1 AS x) SELECT * FROM a")
