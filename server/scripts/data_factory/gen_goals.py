"""目标台账生成（fact_goal）。

先定目标，再由目标反推收入（见 gen_contracts），保证「目标 → 收入 → 完成率」
这条主线在数据层面自洽，问数结果讲得通。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .config import (
    ACHIEVE_MAX,
    ACHIEVE_MIN,
    ACHIEVE_MU,
    ACHIEVE_OVERRIDE,
    ACHIEVE_SIGMA,
    ACHIEVE_YEAR_SHIFT,
    ACHIEVE_YEAR_SHIFT_UNIT,
    GOAL_BASE,
    MONTH_WEIGHTS,
    SOLUTION_GOAL_RATIO_RANGE,
    YEAR_GROWTH,
    YEARS,
)
from .rng import RNG, weighted_split


def _achieve_rate(rng: RNG, unit_code: str, year: int) -> float:
    """计算某单元某年的完成率。刻意制造高/低达成与同比涨跌的样本。"""
    if unit_code in ACHIEVE_OVERRIDE:
        base = ACHIEVE_OVERRIDE[unit_code]
    else:
        base = rng.normal(ACHIEVE_MU, ACHIEVE_SIGMA, ACHIEVE_MIN, ACHIEVE_MAX)
    shift = ACHIEVE_YEAR_SHIFT.get(year, 0.0)
    shift += ACHIEVE_YEAR_SHIFT_UNIT.get(unit_code, {}).get(year, 0.0)
    r = base + shift
    return max(0.30, min(1.30, r))


def generate_goals(w: Any, rng: RNG, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """生成 fact_goal，并返回每个「单元×年度」的目标与计划收入。"""
    w.table("fact_goal", ["unit_code", "year", "month", "biz_goal", "solution_goal"])

    units = ctx["units"]
    plan: Dict[Tuple[str, int], Dict[str, Any]] = {}
    goals_rows: List[List[Any]] = []

    for u in units:
        size = u["size"]
        for year in YEARS:
            # 年度商业目标（cents）
            annual_cents = int(round(GOAL_BASE * 100 * size * (1.0 + YEAR_GROWTH[year])))
            # 按月季节权重拆分，严格求和相等
            monthly = weighted_split(annual_cents, MONTH_WEIGHTS)

            ratio = rng.uniform(*SOLUTION_GOAL_RATIO_RANGE)
            sol_monthly = [int(round(m * ratio)) for m in monthly]

            for m in range(1, 13):
                goals_rows.append(
                    [
                        u["code"],
                        year,
                        m,
                        f"{monthly[m - 1] // 100}.{monthly[m - 1] % 100:02d}",
                        f"{sol_monthly[m - 1] // 100}.{sol_monthly[m - 1] % 100:02d}",
                    ]
                )
            # month = 0 表示年度合计（严格等于 12 个月之和）
            goals_rows.append(
                [
                    u["code"],
                    year,
                    0,
                    f"{annual_cents // 100}.{annual_cents % 100:02d}",
                    f"{sum(sol_monthly) // 100}.{sum(sol_monthly) % 100:02d}",
                ]
            )

            r = _achieve_rate(rng, u["code"], year)
            plan[(u["code"], year)] = {
                "biz_goal_cents": annual_cents,
                "solution_goal_cents": sum(sol_monthly),
                "achieve_rate": r,
                "income_cents": int(round(annual_cents * r)),
                "unit_name": u["name"],
                "size": size,
            }

    w.rows("fact_goal", goals_rows)
    return {"plan": plan}
