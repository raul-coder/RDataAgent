"""数据工厂入口。

用法：
    python -m scripts.data_factory.main                 # 满量造数 + 校验
    python -m scripts.data_factory.main --scale 0.1     # 小数据量（本地开发）
    python -m scripts.data_factory.main --verify-only   # 只跑校验

产物：
    server/data/generated/*.csv             可直接 COPY 进 PostgreSQL
    server/data/generated/_manifest.json    造数参数与「单元×年度」计划收入
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict

from . import verify
from .config import OUTPUT_DIRNAME, SEED, YEARS
from .gen_contracts import generate_contracts
from .gen_dimensions import generate_dimensions
from .gen_goals import generate_goals
from .gen_ppl import generate_ppl
from .gen_system import generate_system
from .rng import RNG
from .writer import CsvWriter


def _default_out_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "data", OUTPUT_DIRNAME))


def build(scale: float = 1.0, seed: int = SEED, out_dir: str = None) -> Dict[str, Any]:
    out_dir = out_dir or _default_out_dir()
    w = CsvWriter(out_dir)
    rng = RNG(seed)

    t0 = time.time()
    ctx = generate_dimensions(w, rng)
    goals = generate_goals(w, rng, ctx)
    plan = goals["plan"]

    contracts = generate_contracts(w, rng, ctx, plan, scale=scale)
    ppl = generate_ppl(w, rng, ctx, scale=scale)
    system = generate_system(w, rng, ctx, plan, scale=scale)

    manifest = {
        "seed": seed,
        "scale": scale,
        "years": list(YEARS),
        "plan": {
            f"{k[0]}|{k[1]}": {
                "biz_goal_cents": v["biz_goal_cents"],
                "solution_goal_cents": v["solution_goal_cents"],
                "achieve_rate": round(v["achieve_rate"], 4),
                "income_cents": v["income_cents"],
                "unit_name": v["unit_name"],
            }
            for k, v in sorted(plan.items())
        },
        "stats": {
            **{k: v for k, v in contracts.items() if k != "actual_income_by_unit_year"},
            **{k: v for k, v in ppl.items() if not isinstance(v, dict)},
            **{k: v for k, v in system.items() if k != "default_password"},
        },
    }
    w.write_manifest(manifest)
    w.close()

    elapsed = time.time() - t0
    return {
        "out_dir": out_dir,
        "elapsed": elapsed,
        "summary": w.counts,
        "manifest": manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="经管之星 · 数据工厂")
    parser.add_argument("--scale", type=float, default=1.0, help="数据量缩放，1.0=满量")
    parser.add_argument("--seed", type=int, default=SEED, help="随机种子")
    parser.add_argument("--out", type=str, default=None, help="输出目录")
    parser.add_argument("--verify-only", action="store_true", help="只执行校验")
    args = parser.parse_args()

    out_dir = args.out or _default_out_dir()

    if args.verify_only:
        for line in verify.run(out_dir):
            print(line)
        return 0

    print(f"造数开始：scale={args.scale} seed={args.seed}")
    print(f"输出目录：{out_dir}")
    result = build(scale=args.scale, seed=args.seed, out_dir=out_dir)
    print(f"\n生成完成，耗时 {result['elapsed']:.1f}s")
    for name, cnt in sorted(result["summary"].items()):
        print(f"  {name.ljust(22)}: {cnt:,} 行")

    print("\n一致性校验：")
    try:
        for line in verify.run(out_dir):
            print("  " + line)
    except verify.VerifyError as e:
        print(f"  [FAIL] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
