"""SQL 校验节点：安全闸门（在 sql_guard 基础上补齐白名单与 LIMIT）。

校验顺序（任一不过直接拒绝并记审计）：
    1. 只读校验（禁止非 SELECT）
    2. 表白名单（必须是 sem_data_source 注册过的对象）
    3. 危险函数 / 系统表
    4. LIMIT 注入
    5. 数据权限注入（行级，服务端强制）
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Sequence

import sqlglot
from sqlglot import exp

from ...core.config import settings
from ...core.exceptions import SQLRejectedError
from ...core.logging import get_logger, log_kv
from ...security.sql_guard import apply_data_permission, assert_readonly

logger = get_logger(__name__)

# 危险函数
DANGEROUS_FUNCS = {
    "pg_sleep", "pg_read_file", "pg_ls_dir", "pg_stat_file",
    "dblink", "dblink_exec", "lo_import", "lo_export",
    "current_setting", "set_config", "query_to_xml",
}

# 禁止访问的系统 schema
FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}


def _cte_aliases(tree: exp.Expression) -> list[exp.Expression]:
    """收集所有 CTE 名称（WITH 子句中的别名）。"""
    out: list[exp.Expression] = []
    for node in tree.walk():
        if isinstance(node, exp.With):
            out.extend(node.expressions or [])
    return out


def validate(
    sql: str,
    allowed_tables: Sequence[str],
    unit_codes: Optional[Sequence[str]] = None,
    *,
    is_ranking: bool = False,
    max_rows: Optional[int] = None,
) -> str:
    """校验并改写 SQL，返回可安全执行的最终语句。

    max_rows：覆盖本次查询的行数上限（默认 settings.SQL_MAX_ROWS）。
    仅台账导出这类明确的大批量操作才需要放开——问数场景保持默认，
    避免一次查询把整表搬进内存。
    """
    # 1) 只读
    assert_readonly(sql)

    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:  # noqa: BLE001
        raise SQLRejectedError(f"SQL 解析失败：{exc}") from exc

    # 2) 表白名单 + 系统 schema
    allowed = {t.lower() for t in allowed_tables}
    # CTE 是"虚拟表"（WITH xxx AS (...) 的 xxx），不是物理表，必须放行
    cte_names = {c.alias_or_name.lower() for c in _cte_aliases(tree)}

    for table in tree.find_all(exp.Table):
        schema = (table.db or "").lower()
        if schema in FORBIDDEN_SCHEMAS:
            raise SQLRejectedError(f"禁止访问系统表：{schema}")
        name = table.name.lower()
        if name in cte_names:
            continue
        qualified = f"{schema}.{name}" if schema else name
        if name not in allowed and qualified not in allowed:
            raise SQLRejectedError(
                f"表 {qualified} 不在白名单内",
                detail={"allowed": sorted(allowed)},
            )

    # 3) 危险函数
    for func in tree.find_all(exp.Anonymous):
        fname = (func.name or "").lower()
        if fname in DANGEROUS_FUNCS:
            raise SQLRejectedError(f"禁止使用函数：{fname}")
    for func in tree.find_all(exp.Func):
        fname = (func.key or "").lower()
        if fname in DANGEROUS_FUNCS:
            raise SQLRejectedError(f"禁止使用函数：{fname}")

    # 3.5) 非法标识符：拦截混入 SQL 的中文列名
    _reject_non_ascii_columns(tree)

    # 4) LIMIT 注入
    _ensure_limit(tree, is_ranking=is_ranking, max_rows=max_rows)
    sql = tree.sql(dialect="postgres")
    log_kv(logger, logging.DEBUG, "LIMIT 注入后", is_ranking=is_ranking, sql=sql)

    # 5) 数据权限（行级，服务端强制）
    before_perm = sql
    sql = apply_data_permission(sql, unit_codes)
    if sql != before_perm:
        # 权限注入改变了 SQL —— 用户看到的「结果少了一截」往往源于此，
        # 结论里会带权限提示（runtime._permission_note），日志里留原始对照。
        log_kv(logger, logging.DEBUG, "数据权限已注入", unit_codes=list(unit_codes or []),
               before=before_perm, after=sql)

    logger.info("SQL 校验通过：%s", sql[:160])
    return sql


_NON_ASCII = re.compile(r"[^\x00-\x7F]")


def _reject_non_ascii_columns(tree: exp.Expression) -> None:
    """拦截含非 ASCII 字符的**列名**。

    为什么需要这道闸：模型偶尔会把思考过程的中文文本泄漏进 SQL，
    例如生成 `f.year = 我们发现2025`（本该是 `f.year = 2025`）。
    sqlglot 会把它解析成一个 Column，最终打到数据库报
    UndefinedColumnError —— 用户看到的是一句看不懂的 PG 报错。

    这里的判断刻意只看 exp.Column：
      - 中文**别名**（`AS 产品线`）是 exp.Alias，合法，必须放行；
      - 中文**字符串字面量**（`= '商业解决方案'`）是 exp.Literal，合法，必须放行。
    只有「被当作列引用却又含中文」才是污染。

    拦下来的收益不只是少一次报错：SQL_REJECTED 会带上原文，
    让自愈重试能拿到明确线索，而不是再错一次。
    """
    for col in tree.find_all(exp.Column):
        name = col.name or ""
        if name and _NON_ASCII.search(name):
            raise SQLRejectedError(
                f"SQL 含非法列名「{name}」：列名只能是英文字段，"
                f"疑似模型把说明文字混进了 SQL",
                detail={"column": name},
            )


def _ensure_limit(
    tree: exp.Expression, *, is_ranking: bool, max_rows: Optional[int] = None
) -> None:
    """为缺少 LIMIT 的最外层查询补上 LIMIT。"""
    default = 10 if is_ranking else 1000
    cap = int(max_rows) if max_rows else settings.SQL_MAX_ROWS

    # 找到最外层的 Select（可能包在 Subquery / Union 里）
    outer = tree
    if isinstance(tree, exp.Subquery):
        outer = tree.this if isinstance(tree.this, (exp.Select, exp.Union)) else tree
    if isinstance(tree, exp.Union):
        outer = tree

    targets = []
    if isinstance(outer, exp.Select):
        targets = [outer]
    elif isinstance(outer, exp.Union):
        targets = list(outer.find_all(exp.Select))

    for sel in targets:
        limit = sel.args.get("limit")
        if limit is None:
            sel.set("limit", exp.Limit(expression=exp.Literal.number(default)))
        else:
            try:
                value = int(limit.expression.this)
                if value > cap:
                    limit.set("expression", exp.Literal.number(cap))
            except (AttributeError, ValueError, TypeError):
                sel.set("limit", exp.Limit(expression=exp.Literal.number(default)))


def looks_like_ranking(sql: str) -> bool:
    """判断是否排名类问题（决定默认 LIMIT）。"""
    import re

    return bool(re.search(r"TOP\s*\d+|排名|前\s*\d+|最多|最高|最低", sql, re.I))
