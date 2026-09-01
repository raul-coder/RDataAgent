"""语义层体检：校验指标/维度的表达式是否引用了真实存在的列。

为什么需要它：
    Text2SQL 的失败大多不是"模型笨"，而是语义层给的配方本身有问题——
    expr_sql 引用了表里不存在的列，或规则里的时间/口径写法只覆盖某一张表。
    模型拿到错误配方只能硬凑，表现为 column "xxx" does not exist，
    或更糟：SQL 能跑但数字不对。这类问题靠等用户报错来发现太被动。

检查项：
    1. 指标 / 维度的 expr_sql 引用的列，在对应表/视图中是否存在
    2. join_sql 引用的表与列是否存在
    3. 规则中提到的列名是否真实存在（粗粒度：抽取带别名的列引用）
    4. 各数据源的表，有哪些列从未被语义层提及（潜在能力缺口）

用法：
    cd server && .venv/bin/python -m scripts.check_semantic
    .venv/bin/python -m scripts.check_semantic --json   # 机器可读输出

退出码：0 = 无问题；1 = 发现错误（便于接入 CI）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from typing import Any

import sqlglot
from sqlglot import exp
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# 与 agent/nodes/retrieve.py 的 ALIASES 保持一致
ALIASES: dict[str, str] = {
    "bi.fact_contract": "f",
    "bi.fact_ppl": "ppl",
    "bi.fact_goal": "g",
    "bi.dim_unit": "d",
    "bi.dim_industry": "i",
    "bi.dim_product": "p",
    "bi.dim_sales": "s",
    "bi.dim_customer": "c",
}

# 聚合函数与关键字，不参与列名校验
_NON_COLUMN_FUNCS = {
    "sum", "count", "avg", "max", "min", "round", "nullif", "coalesce",
    "extract", "cast", "case", "filter", "distinct", "abs", "to_char",
}


async def _load_columns(conn) -> dict[str, set[str]]:
    """加载 bi / public 下所有表与视图的列名。"""
    rows = (
        await conn.execute(
            text(
                "SELECT table_schema || '.' || table_name AS tbl, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema IN ('bi', 'public') "
                "ORDER BY tbl, ordinal_position"
            )
        )
    ).all()
    cols: dict[str, set[str]] = {}
    for tbl, col in rows:
        cols.setdefault(tbl, set()).add(str(col).lower())
    return cols


async def _load_semantics(conn) -> dict[str, list[dict[str, Any]]]:
    """加载语义层：数据源 / 指标 / 维度 / 规则。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for key, sql in (
        ("sources", "SELECT id, object_name, name FROM sem_data_source WHERE enabled = TRUE"),
        ("metrics", "SELECT id, code, name, expr_sql, source_id FROM sem_metric WHERE enabled = TRUE"),
        ("dims", "SELECT id, code, name, expr_sql, display_expr, join_sql, source_id FROM sem_dimension WHERE enabled = TRUE"),
        ("rules", "SELECT id, title, scene, content FROM sem_rule WHERE enabled = TRUE"),
    ):
        res = (await conn.execute(text(sql))).mappings().all()
        out[key] = [dict(r) for r in res]
    return out


# 别名 → 表名的反向映射。注意维表（bi.dim_*）不在 sem_data_source 里，
# 它们是通过维度的 join_sql 引入的，因此这里必须用 ALIASES 反查，
# 只遍历 sources 会漏掉所有 JOIN 维表。
_REVERSE_ALIASES: dict[str, str] = {v: k for k, v in ALIASES.items()}


def _alias_to_table(sources: list[dict], alias: str) -> str | None:
    """把别名解析成表名；裸表名（无别名）原样返回。"""
    if alias in _REVERSE_ALIASES:
        return _REVERSE_ALIASES[alias]
    for s in sources:
        obj = str(s["object_name"])
        short = obj.split(".")[-1]
        if short == alias or obj == alias:
            return obj
    return None


def _check_join(join_sql: str, cols: dict[str, set[str]], sources: list[dict]) -> list[str]:
    """校验 JOIN 语句。

    不能套 `SELECT {join_sql}` 让 sqlglot 解析——'LEFT JOIN' 会被当成
    FROM 子句里的裸标识符，误报成列。这里只需确认：
      1) 引用的表存在
      2) ON 条件里的 alias.column 真实存在
    """
    problems: list[str] = []
    join_sql = join_sql or ""
    if not join_sql:
        return problems

    for tbl in re.findall(r"\bbi\.[a-z_]+", join_sql, re.I):
        if tbl.lower() not in cols:
            problems.append(f"JOIN 引用了不存在的表 {tbl}")

    # ON a.x = b.y 形式的列引用
    for alias, col in re.findall(r"\b([a-z]{1,4})\.([a-z_][a-z0-9_]*)", join_sql):
        tbl = _alias_to_table(sources, alias)
        if tbl is None or tbl not in cols:
            continue
        if col.lower() not in cols[tbl]:
            problems.append(f"JOIN 条件 {alias}.{col}，但表 {tbl} 没有列 '{col}'")
    return problems


def _check_expr(
    expr_sql: str,
    cols: dict[str, set[str]],
    sources: list[dict],
    default_table: str | None,
) -> list[str]:
    """校验一个表达式里的列引用，返回问题列表。"""
    problems: list[str] = []
    sql = (expr_sql or "").strip()
    if not sql:
        return problems
    # 表达式可能是裸列或片段，包一层 SELECT 便于解析
    stmt = sql if sql.upper().startswith("SELECT") else f"SELECT {sql}"
    try:
        tree = sqlglot.parse_one(stmt, read="postgres")
    except Exception:  # noqa: BLE001
        return [f"表达式无法解析：{sql[:60]}"]

    for col in tree.find_all(exp.Column):
        name = (col.name or "").lower()
        if not name:
            continue
        alias = (col.table or "").lower()
        if alias:
            tbl = _alias_to_table(sources, alias)
            if tbl is None:
                problems.append(f"未知表别名 '{alias}.{name}'（不在数据源白名单内）")
                continue
        else:
            tbl = default_table
            if tbl is None:
                continue  # 无法确定归属表，跳过
        if tbl not in cols:
            problems.append(f"表 {tbl} 不存在（引用列 {name}）")
            continue
        if name not in cols[tbl]:
            problems.append(f"表 {tbl} 没有列 '{name}'")
    return problems


async def run(json_out: bool = False) -> int:
    from app.core.config import settings

    engine = create_async_engine(settings.DATABASE_URL)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    findings: list[dict[str, str]] = []
    async with Session() as conn:
        cols = await _load_columns(conn)
        sem = await _load_semantics(conn)

        src_by_id = {s["id"]: s for s in sem["sources"]}

        # 1) 指标
        for m in sem["metrics"]:
            src = src_by_id.get(m.get("source_id"))
            default = str(src["object_name"]) if src else None
            for p in _check_expr(m.get("expr_sql"), cols, sem["sources"], default):
                findings.append({"type": "指标", "ref": f"{m['code']}({m['name']})", "problem": p})

        # 2) 维度（表达式、展示表达式、JOIN 语句）
        for d in sem["dims"]:
            src = src_by_id.get(d.get("source_id"))
            default = str(src["object_name"]) if src else None
            for field, label in (
                ("expr_sql", "表达式"),
                ("display_expr", "展示表达式"),
            ):
                for p in _check_expr(d.get(field), cols, sem["sources"], default):
                    findings.append({"type": f"维度·{label}", "ref": f"{d['code']}({d['name']})", "problem": p})

            for p in _check_join(str(d.get("join_sql") or ""), cols, sem["sources"]):
                findings.append({"type": "维度·JOIN", "ref": d["code"], "problem": p})

        # 3) 规则里带别名的列引用（如 f.land_date、g.month）
        for r in sem["rules"]:
            content = r.get("content") or ""
            for alias, col in re.findall(r"\b([a-z]{1,4})\.([a-z_][a-z0-9_]*)", content):
                if alias in {"e", "g"} and not content.count(f"{alias}.{col}"):
                    continue
                tbl = _alias_to_table(sem["sources"], alias)
                if tbl is None:
                    continue  # 规则里也可能出现示例别名，非白名单的跳过
                if tbl in cols and col.lower() not in cols[tbl]:
                    findings.append({
                        "type": "规则",
                        "ref": f"{r['id']}({r['title']})",
                        "problem": f"提到 {alias}.{col}，但表 {tbl} 没有列 '{col}'",
                    })

        # 4) 覆盖度：数据源的列有多少从未被语义层提及
        mentioned: dict[str, set[str]] = {k: set() for k in cols}
        all_text = " ".join(
            [str(m.get("expr_sql") or "") for m in sem["metrics"]]
            + [str(d.get("expr_sql") or "") + " " + str(d.get("display_expr") or "") for d in sem["dims"]]
            + [str(r.get("content") or "") for r in sem["rules"]]
        ).lower()
        for tbl, names in cols.items():
            for n in names:
                if re.search(rf"\b{re.escape(n)}\b", all_text):
                    mentioned[tbl].add(n)

    # ── 输出 ──────────────────────────────────────────────────────
    coverage = []
    for s in sem["sources"]:
        tbl = str(s["object_name"])
        names = cols.get(tbl, set())
        if not names:
            continue
        un = sorted(n for n in names if n not in mentioned.get(tbl, set()))
        coverage.append({
            "table": tbl,
            "name": s["name"],
            "total": len(names),
            "mentioned": len(names) - len(un),
            "uncovered": un,
        })

    if json_out:
        print(json.dumps({"findings": findings, "coverage": coverage}, ensure_ascii=False, indent=2))
    else:
        print("=" * 78)
        print("语义层体检报告")
        print("=" * 78)

        if findings:
            print(f"\n【问题】共 {len(findings)} 项\n")
            for f in findings:
                print(f"  [{f['type']}] {f['ref']}")
                print(f"      └─ {f['problem']}")
        else:
            print("\n【问题】未发现表达式引用不存在的列 ✅")

        print("\n【覆盖度】数据源中未被语义层任何指标/维度/规则提及的列")
        print("          （这些列模型看不到，问到就只能靠猜）\n")
        for c in coverage:
            if not c["uncovered"]:
                print(f"  {c['table']:<28} {c['mentioned']}/{c['total']} 已覆盖 ✅")
                continue
            print(f"  {c['table']:<28} {c['mentioned']}/{c['total']} 覆盖，未覆盖：")
            print(f"      {', '.join(c['uncovered'])}")

        print("\n" + "=" * 78)

    await engine.dispose()
    return 1 if findings else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="语义层体检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    return asyncio.run(run(json_out=args.json))


if __name__ == "__main__":
    sys.exit(main())
