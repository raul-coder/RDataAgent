"""Few-shot 可执行性校验（语义层质量闸门）。

把 sem_fewshot 中的每条 SQL 在真实数据库上执行，报告：
  - 是否执行成功
  - 返回行数（行数异常往往是 JOIN 扇出或漏过滤的信号）
  - 耗时

这是 I3「Text2SQL 评测集」的前身：语义层内容一旦被改动，
本脚本可立即发现失效样本。

用法：
    python -m scripts.eval.check_fewshots
    python -m scripts.eval.check_fewshots --verbose
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List, Optional, Tuple

from scripts.db_init import _connect, _normalize_dsn

DEFAULT_DSN = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://jingguan:jingguan@localhost:5432/jingguan"
)

# 行数合理性区间：0 行或超过 5 万行都视为可疑
MIN_ROWS = 1
MAX_ROWS = 50000


def fetch_fewshots(cur) -> List[Tuple[int, str, str]]:
    cur.execute("SELECT id, question, sql_text FROM sem_fewshot ORDER BY id")
    return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def run(dsn: str = DEFAULT_DSN, verbose: bool = False) -> int:
    conn, _ = _connect(dsn)
    cur = conn.cursor()

    rows = fetch_fewshots(cur)
    if not rows:
        print("sem_fewshot 为空，请先执行 db_init 装载语义层种子")
        return 1

    print(f"校验 {len(rows)} 条 Few-shot SQL（{os.path.basename(_normalize_dsn(dsn))}）\n")

    failed: List[Tuple[int, str, str]] = []
    suspicious: List[Tuple[int, str, int]] = []
    total_ms = 0

    for fid, question, sql in rows:
        t0 = time.perf_counter()
        try:
            cur.execute(sql)
            result = cur.fetchall() if cur.description else []
            conn.rollback()  # 只读校验，不留下事务
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            failed.append((fid, question, str(exc).splitlines()[0]))
            print(f"  [FAIL] #{fid:<3} {question[:34]:<36} {str(exc).splitlines()[0][:70]}")
            continue

        cost_ms = (time.perf_counter() - t0) * 1000
        total_ms += cost_ms
        n = len(result)

        flag = ""
        if n < MIN_ROWS or n > MAX_ROWS:
            flag = "  ⚠ 行数异常"
            suspicious.append((fid, question, n))

        status = "WARN" if flag else "OK  "
        if verbose or flag:
            print(
                f"  [{status}] #{fid:<3} {question[:34]:<36} "
                f"{n:>6,} 行  {cost_ms:>7.1f}ms{flag}"
            )

    print("\n" + "-" * 78)
    print(f"  成功 {len(rows) - len(failed)}/{len(rows)}   总耗时 {total_ms:.0f}ms   "
          f"平均 {total_ms / max(1, len(rows)):.1f}ms/条")
    if failed:
        print(f"\n  执行失败 {len(failed)} 条：")
        for fid, q, err in failed:
            print(f"    #{fid} {q}\n        -> {err}")
    if suspicious:
        print(f"\n  行数可疑 {len(suspicious)} 条：")
        for fid, q, n in suspicious:
            print(f"    #{fid} {q} -> {n} 行")

    cur.close()
    conn.close()
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="经管之星 · Few-shot 可执行性校验")
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--verbose", action="store_true", help="打印每条明细")
    args = parser.parse_args()
    return run(args.dsn, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
