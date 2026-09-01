"""模型质量对比：用「真实链路」跑 SQL 生成，比较速度 + 质量。

与 probe_models 的区别：
    probe 用简化 prompt 粗测可用性；
    compare 直接调用 sql_generate.generate_sql —— 真实 Schema、
    真实 Prompt（18 指标 / 12 维度 / 15 规则 / Few-shot）、真实解析逻辑，
    因此结论对生产选择才有意义。

用法：
    python -m scripts.eval.compare_models --models deepseek-v4-flash,qwen3.8-max
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time

# 每个用例的"必要条件"：生成的 SQL 必须包含这些特征才算正确
CASES = [
    ("2026年各经营单元收入排名", ["SUM", "GROUP BY", "LIMIT"]),
    ("高风险项目金额统计", ["SUM"]),
    ("2026年每月的合同金额趋势", ["EXTRACT", "GROUP BY"]),
    ("各产品线收入占比", ["SUM", "GROUP BY"]),
]

DEFAULT_MODELS = ["deepseek-v4-pro-0813", "deepseek-v4-flash", "qwen3.8-max"]


async def run_model(model: str, db) -> dict:
    from app.agent.nodes.retrieve import retrieve_schema
    from app.agent.nodes.sql_generate import generate_sql
    from app.llm.litellm_provider import LitellmProvider

    provider = LitellmProvider(model=model)
    schema = await retrieve_schema(db, None)

    rows = []
    total_t = 0.0
    for q, required in CASES:
        t0 = time.perf_counter()
        try:
            draft = await generate_sql(db, [provider], q, schema)
            cost = time.perf_counter() - t0
            total_t += cost
            sql = draft.sql
            up = sql.upper()
            missing = [r for r in required if r.upper() not in up]
            rows.append({"q": q, "ok": not missing, "cost": cost,
                         "missing": missing, "sql": sql, "err": ""})
        except Exception as exc:  # noqa: BLE001
            cost = time.perf_counter() - t0
            total_t += cost
            rows.append({"q": q, "ok": False, "cost": cost,
                         "missing": list(required), "sql": "",
                         "err": f"{type(exc).__name__}: {str(exc)[:70]}"})
    return {"model": model, "rows": rows, "total": total_t}


async def main_async(models: list[str]) -> int:
    sys.path.insert(0, ".")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings

    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    results = []
    async with Session() as db:
        for m in models:
            print(f"  … 正在测试 {m}", flush=True)
            results.append(await run_model(m, db))
    await engine.dispose()

    print("\n" + "=" * 100)
    print(f"{'模型':<24}{'SQL 合格':>9}{'总耗时':>9}   明细")
    print("-" * 100)
    for r in results:
        ok = sum(1 for x in r["rows"] if x["ok"])
        n = len(r["rows"])
        print(f"{r['model']:<24}{f'{ok}/{n}':>9}{r['total']:>8.1f}s")
        for x in r["rows"]:
            mark = "OK  " if x["ok"] else "FAIL"
            note = "" if x["ok"] else f"缺 {x['missing']} {x['err']}"
            print(f"    [{mark}] {x['q']:<24} {x['cost']:>5.1f}s  {note}")
            if x["sql"]:
                print(f"           {x['sql'][:88]}")
    print("=" * 100)

    print("\n结论：")
    ranked = sorted(
        results, key=lambda r: (-(sum(1 for x in r["rows"] if x["ok"])), r["total"])
    )
    for r in ranked:
        ok = sum(1 for x in r["rows"] if x["ok"])
        print(f"  {r['model']:<24} 质量 {ok}/{len(r['rows'])}  耗时 {r['total']:.1f}s")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="模型 SQL 生成质量对比")
    ap.add_argument("--models", default="")
    args = ap.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()] or DEFAULT_MODELS
    return asyncio.run(main_async(models))


if __name__ == "__main__":
    sys.exit(main())
