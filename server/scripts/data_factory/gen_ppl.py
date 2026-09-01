"""PPL 明细台账生成（fact_ppl）：销售机会点管线。

与已签约的合同台账相互独立，用于支撑「高风险项目统计」「商机阶段分布」
等管线类问题。风险概率由所处阶段决定。
"""

from __future__ import annotations

import calendar
from typing import Any, Dict, List, Tuple

from .config import PPL_RISK_BY_STAGE, PPL_STAGES, PPL_TOTAL, YEARS
from .rng import RNG, weighted_split

COLUMNS = [
    "contract_no", "presale_no", "opp_no", "industry_cat", "industry_sub",
    "unit_code", "sales_name", "project_name", "customer_name", "product_type",
    "model", "qty", "amount", "amount_ex_tax", "stage", "risk_level",
    "expect_land_date", "year",
]

# 管线以「当年未落地」为主：2026 权重更高
PPL_YEAR_WEIGHT = {2025: 0.30, 2026: 0.70}

UNIT_PRICE = {"服务器": 8.0, "存储": 5.0, "网络": 2.0}
QTY_RANGE = (1, 400)


def _fmt(cents: int) -> str:
    v = int(cents)
    return f"{v // 100}.{v % 100:02d}"


def generate_ppl(
    w: Any, rng: RNG, ctx: Dict[str, Any], scale: float = 1.0
) -> Dict[str, Any]:
    w.table("fact_ppl", COLUMNS)

    units = ctx["units"]
    products = ctx["products"]
    industries = ctx["industries"]
    customers = ctx["customers"]
    sales_by_unit = ctx["sales_by_unit"]

    stage_names = [s for s, _ in PPL_STAGES]
    stage_weights = [wt for _, wt in PPL_STAGES]

    # 按「单元 × 年度」分配条数
    combos: List[Tuple[str, int]] = [(u["code"], y) for y in YEARS for u in units]
    combo_weights = [
        ctx["unit_by_code"][c]["size"] * PPL_YEAR_WEIGHT[y] for c, y in combos
    ]
    total = int(round(PPL_TOTAL * scale))
    counts = weighted_split(total, combo_weights)

    seq = 0
    risk_counter = {"低": 0, "中": 0, "高": 0}
    stage_counter = {s: 0 for s in stage_names}

    for (unit_code, year), n in zip(combos, counts):
        if n <= 0:
            continue
        sales_pool = sales_by_unit[unit_code]
        for _ in range(n):
            seq += 1
            prod = products[rng.randint(0, len(products) - 1)]
            ind = industries[rng.randint(0, len(industries) - 1)]
            cust = customers[rng.randint(0, len(customers) - 1)]
            sale = sales_pool[rng.randint(0, len(sales_pool) - 1)]

            stage = rng.weighted_choice(stage_names, stage_weights)
            stage_counter[stage] += 1

            p_high, p_mid = PPL_RISK_BY_STAGE[stage]
            x = rng.uniform(0.0, 1.0)
            if x < p_high:
                risk = "高"
            elif x < p_high + p_mid:
                risk = "中"
            else:
                risk = "低"
            risk_counter[risk] += 1

            # 机会金额：对数正态，均值约 120 万元
            amount_cents = max(1000, int(round(rng.lognormal(4.8, 0.9) * 100)))
            amount_ex_tax = int(round(amount_cents / 1.13))

            price = UNIT_PRICE[prod["type"]]
            qty = int(round((amount_cents / 100.0) / price))
            qty = max(QTY_RANGE[0], min(QTY_RANGE[1], qty))

            month = rng.randint(1, 12)
            last_day = calendar.monthrange(year, month)[1]
            expect_land_date = f"{year}-{month:02d}-{rng.randint(1, last_day):02d}"

            w.row(
                "fact_ppl",
                [
                    f"HT-{year}-{seq:06d}",
                    f"YS-{year}-{seq:06d}",
                    f"OPP-{year}-{seq:06d}",
                    ind["cat"],
                    ind["sub"],
                    unit_code,
                    sale["name"],
                    f"{cust['name']}{prod['model']}商机",
                    cust["name"],
                    prod["type"],
                    prod["model"],
                    qty,
                    _fmt(amount_cents),
                    _fmt(amount_ex_tax),
                    stage,
                    risk,
                    expect_land_date,
                    year,
                ],
            )

    return {
        "ppl_rows": seq,
        "ppl_risk_distribution": risk_counter,
        "ppl_stage_distribution": stage_counter,
    }
