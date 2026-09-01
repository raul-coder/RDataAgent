"""数据库初始化与数据装载。

用法：
    export DATABASE_URL=postgresql://jingguan:jingguan@localhost:5432/jingguan
    python -m scripts.db_init                # 建表 + 装载 + 校验
    python -m scripts.db_init --drop         # 先清空业务数据再装载
    python -m scripts.db_init --skip-schema  # 只装载数据

依赖：psycopg（推荐）或 psycopg2-binary，见 pyproject.toml。
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from typing import Dict, Tuple

conn_logger = logging.getLogger("db_init")

SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql")
DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "generated")
)

# 建表脚本（按依赖顺序）
SCHEMA_FILES = (
    "001_schema_sys.sql",
    "002_schema_sem.sql",
    "003_schema_bi.sql",
    "004_views.sql",
    "007_dict_column.sql",   # 数据字典（台账列口径，含种子数据）
)
# 语义层种子（须在数据装载后执行，因其依赖数据源 id）
SEED_FILES = (
    "005_seed_semantic.sql",
    "006_seed_fewshot_extra.sql",   # Few-shot 扩充 20 → 50
)

# 装载顺序：先维度后事实，先主表后子表
LOAD_ORDER: Tuple[str, ...] = (
    # bi 维度
    "dim_unit", "dim_industry", "dim_product", "dim_sales", "dim_customer", "dim_date",
    # bi 事实
    "fact_goal", "fact_ppl", "fact_contract",
    # 系统
    "sys_role", "sys_user", "sys_user_role", "sys_menu", "sys_role_menu",
    "sys_role_data_perm", "sys_app_config", "sys_model", "sys_oper_log",
    # 会话与反馈
    "chat_session", "chat_message", "qa_feedback", "quick_question",
)

# 属于 bi schema 的表（其余在 public）
BI_TABLES = frozenset(
    {
        "dim_unit", "dim_industry", "dim_product", "dim_sales", "dim_customer",
        "dim_date", "fact_goal", "fact_ppl", "fact_contract",
    }
)


def _qualify(table: str) -> str:
    """业务表需要显式带 schema，否则 COPY 会在 public 中找不到。"""
    if table in BI_TABLES and "." not in table:
        return f"bi.{table}"
    return table

# 需要同步序列的表（含 BIGSERIAL 主键）
SEQUENCE_TABLES: Tuple[str, ...] = (
    "sys_user", "sys_role", "sys_menu", "sys_role_data_perm", "sys_model",
    "sys_oper_log", "sys_prompt_template", "chat_session", "chat_message",
    "chat_message_feedback", "qa_feedback", "quick_question", "chat_query_trace",
    "sem_data_source", "sem_metric", "sem_dimension", "sem_rule", "sem_fewshot",
    "bi.fact_contract", "bi.fact_ppl", "bi.fact_goal",
)


def _normalize_dsn(dsn: str) -> str:
    """把 SQLAlchemy 风格的 DSN 转成驱动可直接使用的 DSN。

    例：postgresql+asyncpg://user:pwd@host/db -> postgresql://user:pwd@host/db
    这样脚本可以直接复用 .env 里的 DATABASE_URL。
    """
    return dsn.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def _connect(dsn: str):
    dsn = _normalize_dsn(dsn)
    try:
        import psycopg  # psycopg3
        return psycopg.connect(dsn), "psycopg3"
    except ImportError:
        pass
    try:
        import psycopg2  # psycopg2
        return psycopg2.connect(dsn), "psycopg2"
    except ImportError:
        print(
            "缺少数据库驱动，请先安装：pip install 'psycopg[binary]'  或  pip install psycopg2-binary",
            file=sys.stderr,
        )
        sys.exit(2)


def _exec_sql_file(cur, path: str) -> None:
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()
    try:
        cur.execute(sql)
    except Exception as e:  # noqa: BLE001 - 需要把文件上下文带给用户
        raise RuntimeError(f"执行 {os.path.basename(path)} 失败：{e}") from e


def _copy_csv(cur, table: str, path: str, driver: str) -> int:
    target = _qualify(table)
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        cols = ", ".join(f'"{c}"' for c in header)
        if driver == "psycopg3":
            with cur.copy(
                f"COPY {target} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER false)"
            ) as cp:
                for line in f:
                    cp.write(line)
        else:
            cur.copy_expert(
                f"COPY {target} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER false)", f
            )
    # 用 csv.reader 计数，避免多行列值（含换行的回答文本）被按物理行重复统计
    with open(path, "r", encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def _setval_all(cur) -> None:
    for t in SEQUENCE_TABLES:
        cur.execute(
            """
            SELECT setval(
                pg_get_serial_sequence(%s, 'id'),
                COALESCE((SELECT MAX(id) FROM {t}), 1)
            )
            """.format(t=t),
            (t,),
        )


def _grant_readonly(cur) -> None:
    """为只读账号授权（表由本脚本创建，故在此授权）。

    权限范围刻意做「最小够用」：
      - bi schema 全部表：问数取数的目标
      - public.sem_*  ：语义层元数据，Agent 生成 SQL 时必须读取
    """
    try:
        cur.execute("GRANT USAGE ON SCHEMA bi TO bi_readonly")
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA bi TO bi_readonly")
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA bi GRANT SELECT ON TABLES TO bi_readonly"
        )

        cur.execute("GRANT USAGE ON SCHEMA public TO bi_readonly")
        for t in ("sem_data_source", "sem_metric", "sem_dimension", "sem_rule",
                  "sem_fewshot", "sem_dict_column"):
            cur.execute(f"GRANT SELECT ON public.{t} TO bi_readonly")

        print("  [grant] bi_readonly：bi schema 全部表 + public.sem_* 元数据，只读")
    except Exception as exc:  # noqa: BLE001 - 角色不存在时跳过，不影响主流程
        conn_logger.warning("跳过只读授权（%s）。生产环境请先创建 bi_readonly 角色", exc)


def run(
    dsn: str,
    data_dir: str = DATA_DIR,
    drop: bool = False,
    skip_schema: bool = False,
    grant_only: bool = False,
) -> int:
    if not os.getenv("DATABASE_URL"):
        os.environ["DATABASE_URL"] = dsn  # 便于脚本内部/log 复用
    conn, driver = _connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()
    t0 = time.time()
    loaded: Dict[str, int] = {}

    if grant_only:
        _grant_readonly(cur)
        conn.commit()
        cur.close()
        conn.close()
        return 0

    try:
        if not skip_schema:
            for name in SCHEMA_FILES:
                _exec_sql_file(cur, os.path.join(SQL_DIR, name))
                print(f"  [schema] {name}")
            conn.commit()

        if drop:
            cur.execute(
                """
                TRUNCATE TABLE chat_message, chat_session, qa_feedback, quick_question,
                    sys_oper_log, sys_model, sys_app_config, sys_role_data_perm, sys_role_menu,
                    sys_user_role, sys_menu, sys_user, sys_role,
                    sem_fewshot, sem_rule, sem_dimension, sem_metric, sem_data_source,
                    bi.fact_contract, bi.fact_ppl, bi.fact_goal,
                    bi.dim_date, bi.dim_customer, bi.dim_sales, bi.dim_product,
                    bi.dim_industry, bi.dim_unit
                RESTART IDENTITY CASCADE
                """
            )
            print("  [reset] 已清空全部业务数据")

        for table in LOAD_ORDER:
            path = os.path.join(data_dir, f"{table}.csv")
            if not os.path.exists(path):
                print(f"  [skip ] {table}（无 CSV，请先执行造数）")
                continue
            n = _copy_csv(cur, table, path, driver)
            loaded[table] = n
            print(f"  [load ] {table.ljust(20)} {n:>8,} 行")

        for name in SEED_FILES:
            _exec_sql_file(cur, os.path.join(SQL_DIR, name))
            print(f"  [seed ] {name}")

        _setval_all(cur)
        _grant_readonly(cur)
        conn.commit()

        cur.execute("ANALYZE")
        conn.commit()

        # ── 装载后校验 ──────────────────────────────────────────────
        print("\n装载后校验：")
        cur.execute(
            """
            SELECT (SELECT COUNT(*) FROM bi.fact_contract),
                   (SELECT COUNT(*) FROM bi.fact_ppl),
                   (SELECT COUNT(*) FROM bi.fact_goal),
                   (SELECT COUNT(*) FROM sys_user),
                   (SELECT COUNT(*) FROM sem_metric),
                   (SELECT COUNT(*) FROM sem_fewshot)
            """
        )
        c_fc, c_ppl, c_goal, c_user, c_metric, c_few = cur.fetchone()
        print(f"  fact_contract={c_fc:,}  fact_ppl={c_ppl:,}  fact_goal={c_goal:,}")
        print(f"  sys_user={c_user:,}  sem_metric={c_metric:,}  sem_fewshot={c_few:,}")

        cur.execute(
            """
            SELECT COUNT(*) FROM bi.fact_contract c
            WHERE c.year_income <> (c.m1_income + c.m2_income + c.m3_income + c.m4_income
                                  + c.m5_income + c.m6_income + c.m7_income + c.m8_income
                                  + c.m9_income + c.m10_income + c.m11_income + c.m12_income)
            """
        )
        bad = cur.fetchone()[0]
        if bad:
            raise RuntimeError(f"有 {bad} 条合同的年度收入 ≠ 分月之和")
        print("  [OK] Σ分月收入 = 年度收入（fact_contract）")

        cur.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT a.unit_code, a.year
                FROM bi.fact_goal a
                JOIN bi.fact_goal b
                  ON b.unit_code = a.unit_code AND b.year = a.year AND b.month = 0
                WHERE a.month > 0
                GROUP BY a.unit_code, a.year, b.biz_goal, b.solution_goal
                HAVING ABS(b.biz_goal - SUM(a.biz_goal)) > 0.01
                    OR ABS(b.solution_goal - SUM(a.solution_goal)) > 0.01
            ) t
            """
        )
        bad = cur.fetchone()[0]
        if bad:
            raise RuntimeError(f"有 {bad} 组目标：年度 ≠ 月度之和")
        print("  [OK] Σ月度目标 = 年度目标（fact_goal）")

        cur.execute(
            "SELECT COUNT(*), ROUND(SUM(income)/NULLIF(SUM(biz_goal),0)*100, 1) "
            "FROM bi.v_overall_achieve WHERE year = 2026"
        )
        n_units, rate = cur.fetchone()
        if rate is None or not (30.0 <= float(rate) <= 130.0):
            # 常见于「目标表 JOIN 事实表」导致行扇出，使 biz_goal 被放大
            raise RuntimeError(
                f"2026 整体完成率 {rate}% 超出合理区间 [30, 130]，"
                "请检查报表视图是否存在 JOIN 扇出"
            )
        print(f"  [OK] v_overall_achieve(2026)：{n_units} 个经营单元，整体完成率 {rate}%")

        cur.execute(
            """
            SELECT v.unit_name, v.achieve_rate FROM bi.v_overall_achieve v
            WHERE v.year = 2026 ORDER BY v.achieve_rate ASC LIMIT 3
            """
        )
        print("  完成率最低 3 个单元：" + "，".join(f"{n} {r}%" for n, r in cur.fetchall()))

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    print(f"\n完成，耗时 {time.time() - t0:.1f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="经管之星 · 数据库初始化")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL", ""), help="PostgreSQL DSN")
    parser.add_argument("--data-dir", default=DATA_DIR, help="造数 CSV 目录")
    parser.add_argument("--drop", action="store_true", help="装载前清空业务数据")
    parser.add_argument("--skip-schema", action="store_true", help="跳过建表")
    parser.add_argument("--grant-only", action="store_true", help="只执行只读账号授权")
    args = parser.parse_args()

    if not args.dsn:
        print("请通过 --dsn 或环境变量 DATABASE_URL 指定数据库连接", file=sys.stderr)
        return 2
    return run(
        args.dsn,
        data_dir=args.data_dir,
        drop=args.drop,
        skip_schema=args.skip_schema,
        grant_only=args.grant_only,
    )


if __name__ == "__main__":
    sys.exit(main())
