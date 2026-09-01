"""SQL 安全护栏（I1：数据权限注入；I2 将补齐白名单等完整校验）。

为什么必须在服务端注入：
    前端传来的任何"可见范围"都不可信。行级数据权限必须在
    SQL 执行前由服务端改写，才能做到「即便模型生成的 SQL 遗漏了过滤条件，
    也拿不到越权数据」。
"""

from __future__ import annotations

import functools
from typing import List, Optional, Sequence, Set

import sqlglot
from sqlglot import exp

from ..core.exceptions import SQLRejectedError
from ..core.logging import get_logger

logger = get_logger(__name__)

# 含 unit_code 列、需要施加经营单元数据权限的对象
UNIT_BEARING_OBJECTS = frozenset(
    {
        "fact_contract",
        "fact_ppl",
        "fact_goal",
        "v_overall_achieve",
        "v_product_analysis",
        "v_solution_analysis",
        "v_industry_achieve",
        "v_key_unit",
        "v_achieve_yoy",
    }
)

UNIT_COLUMN = "unit_code"


def extract_tables(sql: str) -> Set[str]:
    """提取 SQL 中引用的全部表名（不含 schema）。"""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:  # noqa: BLE001
        raise SQLRejectedError(f"SQL 解析失败：{exc}") from exc
    return {t.name for t in tree.find_all(exp.Table)}


def _from_clause(select: exp.Select):
    """兼容 sqlglot 版本差异：>=30 用 'from_'，旧版用 'from'。"""
    return select.args.get("from_") or select.args.get("from")


def _source_aliases(select: exp.Select, objects: frozenset) -> List[str]:
    """返回该 SELECT 自己的 FROM/JOIN 中命中受限对象的别名（不含子查询内的表）。"""
    aliases: List[str] = []
    sources = []
    from_clause = _from_clause(select)
    if from_clause is not None and getattr(from_clause, "this", None) is not None:
        sources.append(from_clause.this)
    for join in select.args.get("joins") or []:
        if join.this is not None:
            sources.append(join.this)

    for src in sources:
        if isinstance(src, exp.Table) and src.name in objects:
            aliases.append(src.alias_or_name)
    return aliases


def _leaf_unit_selects(tree: exp.Expression) -> List[exp.Select]:
    """找出需要注入条件的 SELECT：自身引用受限对象，且其子查询不再引用。

    这样能保证条件注入在「离表最近」的一层，
    既不会漏过滤，也不会因为多层重复注入而改变语义。
    """
    result: List[exp.Select] = []
    for select in tree.find_all(exp.Select):
        if not _source_aliases(select, UNIT_BEARING_OBJECTS):
            continue
        # find_all 会包含自身，必须排除，否则任何命中都会被误判为"非叶子"
        nested_has_unit = any(
            _source_aliases(sub, UNIT_BEARING_OBJECTS)
            for sub in select.find_all(exp.Select)
            if sub is not select
        )
        if not nested_has_unit:
            result.append(select)
    return result


def _build_condition(aliases: Sequence[str], unit_codes: Sequence[str]) -> exp.Expression:
    literals = [exp.Literal.string(c) for c in unit_codes]
    conds = [
        exp.In(this=exp.column(UNIT_COLUMN, table=alias), expressions=list(literals))
        for alias in aliases
    ]
    return functools.reduce(lambda a, b: exp.or_(a, b), conds)


def _add_where(select: exp.Select, condition: exp.Expression) -> None:
    where = select.args.get("where")
    if where is None:
        select.set("where", exp.Where(this=condition))
    else:
        where.set("this", exp.and_(where.this, condition, dialect="postgres"))


def apply_data_permission(sql: str, unit_codes: Optional[Sequence[str]]) -> str:
    """把经营单元可见范围注入 SQL。

    :param unit_codes: 允许的经营单元编码；None 或空列表表示不限制，原样返回
    :raises SQLRejectedError: SQL 解析失败，或引用了受限对象却无法注入条件
    """
    if not unit_codes:
        return sql

    codes = [str(c) for c in unit_codes if c]
    if not codes:
        return sql

    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:  # noqa: BLE001
        raise SQLRejectedError(f"SQL 解析失败：{exc}") from exc

    targets = _leaf_unit_selects(tree)
    if not targets:
        tables = extract_tables(sql)
        if tables & UNIT_BEARING_OBJECTS:
            # 引用了受限表却定位不到注入点：宁可拒绝，也不能放行越权查询
            raise SQLRejectedError(
                "无法施加数据权限，已拒绝执行",
                detail={"tables": sorted(tables)},
            )
        return sql  # 与经营单元无关的查询（如纯维度表），无需注入

    for select in targets:
        aliases = _source_aliases(select, UNIT_BEARING_OBJECTS)
        _add_where(select, _build_condition(aliases, codes))

    logger.info(
        "数据权限已注入：%d 个经营单元，命中 %d 个查询块", len(codes), len(targets)
    )
    return tree.sql(dialect="postgres")


# ── 基础只读校验（完整白名单校验在 I2 的 SQLValidator 中实现）──────
FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
    exp.Alter, exp.Grant, exp.Copy, exp.Command,
)


def assert_readonly(sql: str) -> None:
    """拒绝任何非查询语句。"""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:  # noqa: BLE001
        raise SQLRejectedError(f"SQL 解析失败：{exc}") from exc

    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            raise SQLRejectedError(
                f"禁止执行 {type(node).__name__} 语句，仅允许 SELECT"
            )
