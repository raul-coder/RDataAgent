"""台账查询服务（T5-3 / FR-D1）。

安全模型与问数链路**共用同一套闸门**，不另起炉灶：
    1. 表名白名单（3 张台账，其余一律拒绝）
    2. 列名白名单（来自数据字典 sem_dict_column，不接受前端任意列名）
    3. 用户输入值一律经 sqlglot Literal 转义
    4. 复用 sql_validate.validate：只读校验 / 危险函数 / LIMIT 上限
    5. apply_data_permission 注入经营单元行级权限
    6. 最终用只读账号（bi_readonly）执行，即使注入被绕过也写不了库

行级权限为什么必须施加：台账是明细数据（合同号、金额、客户），
不像聚合结果那样"看不出个体"，漏过滤就是实打实的数据泄露。
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from ..agent.nodes.sql_validate import validate as validate_sql
from ..core.exceptions import AppException, ErrorCode
from ..core.logging import get_logger
from ..models import SemDictColumn

logger = get_logger(__name__)

# 台账白名单：key -> (物理表, 中文标题, 菜单 id)。
# 前端只能传 key，杜绝表名注入；菜单 id 用于取该台账的数据权限
# （sys_role_data_perm 按「菜单 × 经营单元」配置，与问数菜单是分开的两套授权）。
LEDGERS: dict[str, tuple[str, str, int]] = {
    "contract": ("bi.fact_contract", "商业市场台账", 13),
    "ppl": ("bi.fact_ppl", "PPL 明细台账", 14),
    "goal": ("bi.fact_goal", "整体目标台账", 15),
}

# 允许的筛选操作符（白名单，不接受任意 SQL 片段）
_FILTER_OPS = {"eq", "ne", "in", "contains", "gte", "lte", "between"}
_SORT_DIRS = {"asc", "desc"}

MAX_PAGE_SIZE = 200
# 导出上限：问数默认的 SQL_MAX_ROWS=5000 对「导出整张台账」太紧，
# 但也不能无上限（把整表搬进内存会拖垮服务），取一个明确的兜底值。
EXPORT_MAX_ROWS = 50000
# 枚举 / 维表列的候选值上限，避免高基数列（如合同号）拖垮接口
_MAX_ENUM_VALUES = 300

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _table_of(key: str) -> str:
    if key not in LEDGERS:
        raise AppException(
            f"未知台账：{key}", ErrorCode.BAD_REQUEST,
            detail={"allowed": sorted(LEDGERS)},
        )
    return LEDGERS[key][0].split(".")[-1]


def _qualified(key: str) -> str:
    return LEDGERS[key][0]


def _menu_id(key: str) -> int:
    """台账对应的菜单 id（用于取数据权限）。"""
    return LEDGERS[key][2]


def visible_units(user, key: str) -> Optional[list[str]]:
    """当前用户在该台账上的可见经营单元；超管或无权配置返回 None（不限制）。"""
    _table_of(key)  # 顺带校验 key 合法
    if getattr(user, "is_superadmin", False):
        return None
    raw = (user.data_perms or {}).get(str(_menu_id(key)))
    if raw is None:
        return None
    return list(raw) if raw else None


# ── 值转义：所有用户输入都必须过这一关 ────────────────────────────
def _lit(value: Any, data_type: str) -> str:
    """把用户输入转成安全的 SQL 字面量。

    用 sqlglot 的 Literal 而非手工拼引号，单引号转义交给库处理。
    """
    if value is None or value == "":
        return "NULL"

    if data_type == "bool":
        truthy = value in (True, 1, "1", "true", "True", "是")
        return "TRUE" if truthy else "FALSE"

    if data_type == "number":
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise AppException(f"数值筛选条件不合法：{value!r}", ErrorCode.BAD_REQUEST) from exc
        return exp.Literal.number(num).sql(dialect="postgres")

    sval = str(value)
    if data_type == "date" and not _DATE_RE.match(sval):
        raise AppException(f"日期需为 YYYY-MM-DD：{value!r}", ErrorCode.BAD_REQUEST)
    return exp.Literal.string(sval).sql(dialect="postgres")


def _condition(expr: str, op: str, data_type: str, value: Any) -> Optional[str]:
    """按操作符生成单个 WHERE 条件；无法生成时返回 None（忽略该条件）。"""
    if op not in _FILTER_OPS:
        raise AppException(f"不支持的筛选操作：{op}", ErrorCode.BAD_REQUEST)

    if op == "eq":
        return f"{expr} = {_lit(value, data_type)}"
    if op == "ne":
        return f"{expr} <> {_lit(value, data_type)}"
    if op == "in":
        vals = value if isinstance(value, (list, tuple)) else [value]
        vals = [v for v in vals if v is not None and v != ""]
        if not vals:
            return None
        inner = ", ".join(_lit(v, data_type) for v in vals)
        return f"{expr} IN ({inner})"
    if op == "contains":
        return f"{expr} LIKE {_lit(f'%{value}%', 'text')}"
    if op == "gte":
        return f"{expr} >= {_lit(value, data_type)}"
    if op == "lte":
        return f"{expr} <= {_lit(value, data_type)}"
    if op == "between":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise AppException("between 需要 [min, max] 两个值", ErrorCode.BAD_REQUEST)
        lo, hi = value
        if lo in (None, ""):
            return _condition(expr, "lte", data_type, hi)
        if hi in (None, ""):
            return _condition(expr, "gte", data_type, lo)
        return f"{expr} BETWEEN {_lit(lo, data_type)} AND {_lit(hi, data_type)}"
    return None


# ── 元数据 ────────────────────────────────────────────────────────
async def _meta(db: AsyncSession, key: str) -> list[dict]:
    """读取列定义（含维表关联信息，供内部构造 JOIN 用）。"""
    table = _table_of(key)
    rows = (
        await db.execute(
            select(SemDictColumn)
            .where(SemDictColumn.table_name == table)
            .order_by(SemDictColumn.sort_order, SemDictColumn.id)
        )
    ).scalars().all()
    if not rows:
        raise AppException(
            f"台账 {key} 尚未配置数据字典", ErrorCode.NOT_FOUND
        )
    return [
        {
            "column": r.column_name,
            "cn_name": r.cn_name,
            "data_type": r.data_type,
            "caliber": r.caliber or "",
            "ref_table": r.ref_table,
            "ref_key": r.ref_key,
            "ref_label": r.ref_label,
            "visible": bool(r.visible),
            "filterable": bool(r.filterable),
            "sortable": bool(r.sortable),
        }
        for r in rows
    ]


async def columns(
    db: AsyncSession,
    ro: AsyncSession,
    key: str,
    *,
    with_values: bool = False,
) -> list[dict]:
    """列定义（给前端渲染表头与筛选器）。

    with_values：为枚举列与编码列附带候选值，让筛选器能下拉选择，
    否则用户得手输「上海代表处」——既难用又容易打错。
    高基数列（合同号等）不返回候选值。
    """
    meta = await _meta(db, key)
    out = [
        {
            "column": m["column"],
            "cn_name": m["cn_name"],
            "data_type": m["data_type"],
            "caliber": m["caliber"],
            "visible": m["visible"],
            "filterable": m["filterable"],
            "sortable": m["sortable"],
            "values": [],
        }
        for m in meta
    ]

    if not with_values:
        return out

    for m, item in zip(meta, out):
        # 只有枚举列和"编码->名称"列才值得下拉；其余手输
        if m["data_type"] != "enum" and not m["ref_table"]:
            continue
        item["values"] = await _distinct(ro, m, key)
    return out


async def _distinct(ro: AsyncSession, col: dict, key: str) -> list[Any]:
    """取列的候选值（编码列返回名称，便于用户按名称筛选）。"""
    table = _qualified(key)
    try:
        if col["ref_table"]:
            # 编码列：候选值来自维表的名称
            sql = (
                f"SELECT DISTINCT {col['ref_label']} FROM {col['ref_table']} "
                f"WHERE {col['ref_label']} IS NOT NULL "
                f"ORDER BY {col['ref_label']} LIMIT {_MAX_ENUM_VALUES}"
            )
        else:
            sql = (
                f"SELECT DISTINCT t.{col['column']} FROM {table} AS t "
                f"WHERE t.{col['column']} IS NOT NULL "
                f"ORDER BY t.{col['column']} LIMIT {_MAX_ENUM_VALUES}"
            )
        rows = (await ro.execute(text(sql))).scalars().all()
        return [r for r in rows]
    except Exception as exc:  # noqa: BLE001
        # 候选值只是体验增强，取不到不影响台账本身
        logger.warning("取列 %s 的候选值失败：%s", col["column"], exc)
        return []


# ── 查询 ──────────────────────────────────────────────────────────
def _build_sql(
    key: str,
    meta: list[dict],
    columns: Optional[list[str]],
    filters: list[dict],
    sort_by: Optional[str],
    sort_dir: str,
    limit: int,
    offset: int,
) -> tuple[str, list[str]]:
    """构造 SELECT，返回 (sql, 允许的表清单)。"""
    table = _qualified(key)
    by_name = {m["column"]: m for m in meta}

    # 1) 目标列：默认取数据字典中 visible 的列
    if columns:
        unknown = [c for c in columns if c not in by_name]
        if unknown:
            raise AppException(
                f"未知列：{unknown}", ErrorCode.BAD_REQUEST,
                detail={"allowed": sorted(by_name)},
            )
        targets = [by_name[c] for c in columns]
    else:
        targets = [m for m in meta if m["visible"]]
    if not targets:
        raise AppException("没有可查询的列", ErrorCode.BAD_REQUEST)

    # 2) 维表 JOIN（对所有编码列都建，未展示的列可能仍参与筛选/排序）
    joins: list[str] = []
    allowed: list[str] = [table]
    expr_of: dict[str, str] = {}
    for m in meta:
        name = m["column"]
        if m["ref_table"]:
            alias = f"r{len(joins)}"
            joins.append(
                f"LEFT JOIN {m['ref_table']} AS {alias} "
                f"ON {alias}.{m['ref_key']} = t.{name}"
            )
            allowed.append(m["ref_table"])
            # 编码列展示名称：用户看到"上海代表处"而非"SH"
            expr_of[name] = f"COALESCE({alias}.{m['ref_label']}, t.{name})"
        else:
            expr_of[name] = f"t.{name}"

    # 3) 投影（用窗口函数带出总数，省一次 COUNT 查询）
    projection = ["COUNT(*) OVER () AS __total"] + [
        f"{expr_of[m['column']]} AS {m['column']}" for m in targets
    ]

    sql = (
        f"SELECT {', '.join(projection)} "
        f"FROM {table} AS t "
        f"{' '.join(joins)}"
    )

    # 4) 筛选
    conds: list[str] = []
    for f in filters:
        name = str(f.get("column", ""))
        col = by_name.get(name)
        if not col:
            raise AppException(f"未知筛选列：{name}", ErrorCode.BAD_REQUEST)
        if not col["filterable"]:
            raise AppException(f"列 {name} 不支持筛选", ErrorCode.BAD_REQUEST)
        cond = _condition(
            expr_of[name], str(f.get("op", "eq")), col["data_type"], f.get("value")
        )
        if cond:
            conds.append(cond)
    if conds:
        sql += " WHERE " + " AND ".join(conds)

    # 5) 排序（列名同样走白名单）
    if sort_by:
        if sort_by not in by_name:
            raise AppException(f"未知排序列：{sort_by}", ErrorCode.BAD_REQUEST)
        if not by_name[sort_by]["sortable"]:
            raise AppException(f"列 {sort_by} 不支持排序", ErrorCode.BAD_REQUEST)
        direction = sort_dir.lower()
        if direction not in _SORT_DIRS:
            raise AppException(f"排序方向只能是 asc/desc：{sort_dir}", ErrorCode.BAD_REQUEST)
        sql += f" ORDER BY {by_name[sort_by]['column']} {direction.upper()}"

    sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return sql, allowed


async def query(
    db: AsyncSession,
    ro: AsyncSession,
    key: str,
    *,
    filters: Optional[list[dict]] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 20,
    columns: Optional[list[str]] = None,
    unit_codes: Optional[Sequence[str]] = None,
) -> dict:
    """分页查询台账。"""
    meta = await _meta(db, key)

    page = max(1, int(page))
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    offset = (page - 1) * page_size

    sql, allowed = _build_sql(
        key, meta, columns, filters or [], sort_by, sort_dir, page_size, offset
    )
    final = validate_sql(sql, allowed, unit_codes)

    res = await ro.execute(text(final))
    raw = [list(r) for r in res.fetchall()]
    total = int(raw[0][0]) if raw else 0
    rows = [r[1:] for r in raw]

    proj = columns or [m["column"] for m in meta if m["visible"]]
    return {
        "columns": proj,
        "rows": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sql": final,   # 便于用户在"查看 SQL"里核对口径
    }


async def export_rows(
    db: AsyncSession,
    ro: AsyncSession,
    key: str,
    *,
    filters: Optional[list[dict]] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc",
    columns: Optional[list[str]] = None,
    unit_codes: Optional[Sequence[str]] = None,
    max_rows: int = EXPORT_MAX_ROWS,
) -> tuple[list[str], list[list[Any]], list[dict], bool]:
    """导出：与查询同条件，但不分页。

    返回 (headers, rows, meta, truncated)。truncated 表示已触及上限，
    由接口层回传给前端提示，避免用户拿着截断的文件以为是全量。
    """
    meta = await _meta(db, key)
    sql, allowed = _build_sql(
        key, meta, columns, filters or [], sort_by, sort_dir, max_rows, 0
    )
    final = validate_sql(sql, allowed, unit_codes, max_rows=max_rows)

    res = await ro.execute(text(final))
    raw = [list(r[1:]) for r in res.fetchall()]

    proj = columns or [m["column"] for m in meta if m["visible"]]
    meta_of = {m["column"]: m for m in meta}
    head_meta = [{"column": c, "cn_name": meta_of[c]["cn_name"]} for c in proj]
    return proj, raw, head_meta, len(raw) >= max_rows
