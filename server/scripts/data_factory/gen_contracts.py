"""商业市场台账生成（fact_contract）。

由「单元年度计划收入」反推合同明细：
  计划收入 = 年度商业目标 × 完成率
  合同金额 = 计划收入按对数正态权重拆分（长尾：少数大单、多数小单）
  分月收入 = 合同金额按 [50%, 30%, 20%] 在落地月起 3 个月内确认
  分月回款 = 合同金额 × Beta(6,2) 回款比例，滞后落地月 1~3 个月

所有金额以 cents（万元的百分之一）为整数运算，
保证 Σ分月 = Σ分季 = 年度、Σ合同 = 计划收入 严格相等。
"""

from __future__ import annotations

import calendar
from typing import Any, Dict, List, Sequence, Tuple

from .config import (
    BIG_DEAL_PROFILE,
    BIG_DEAL_RATIO,
    BIG_DEAL_TARGET_SHARE,
    CONTRACT_AMOUNT_SIGMA,
    CONTRACT_TOTAL,
    GROSS_MARGIN_RANGE,
    MONTH_WEIGHTS,
    PAYMENT_LAG_RANGE,
    PAYMENT_RATIO_BETA,
    QTY_RANGE,
    REBATE_RATE,
    REVENUE_SPREAD,
    RISK_AMOUNT_COEF,
    RISK_BASE,
    RISK_MAX,
    RISK_MONTH_COEF,
    TAX_RATE,
    YEAR_GROWTH,
    YEARS,
)
from .rng import RNG, weighted_split

# 合同金额下限（cents）：1 万元。低于此值的"合同"不符合企业销售常识，
# 且下限若被大量触底，会让金额分布失真（中位数被钉在下限上）。
MIN_AMOUNT_CENTS = 100

# 单价（万元/台套），用于把金额换算成合理的台套数
UNIT_PRICE = {"服务器": 8.0, "存储": 5.0, "网络": 2.0}

PROJECT_NAME_SUFFIX = (
    "采购项目",
    "扩容项目",
    "信创替代项目",
    "数据中心建设项目",
)

MONTH_INCOME_COLS = [f"m{i}_income" for i in range(1, 13)]
QUARTER_INCOME_COLS = [f"q{i}_income" for i in range(1, 5)]
MONTH_PAYMENT_COLS = [f"m{i}_payment" for i in range(1, 13)]
QUARTER_PAYMENT_COLS = [f"q{i}_payment" for i in range(1, 5)]

COLUMNS = (
    ["contract_no", "presale_no", "opp_no", "unit_code", "industry_code", "product_code",
     "sales_id", "customer_code", "project_name", "qty", "amount", "amount_ex_tax",
     "cost", "gross_profit", "land_date", "risk_level", "is_new_product", "rebate_flag"]
    + MONTH_INCOME_COLS
    + QUARTER_INCOME_COLS
    + ["year_income"]
    + MONTH_PAYMENT_COLS
    + QUARTER_PAYMENT_COLS
    + ["year_payment", "remark", "year"]
)


def _split_with_min(
    total: int, weights: Sequence[float], min_val: int
) -> List[int]:
    """按权重拆分并保证每份不小于 min_val，且求和严格等于 total。

    两步走：
      1) 把低于下限的份数抬到下限，记录因此多出来的总量 deficit；
      2) 从「高于下限」的份数中按从大到小扣回 deficit。
    由于调用前已保证 total >= n * min_val，第 2 步一定能扣完。
    """
    n = len(weights)
    if n == 0:
        return []
    if total < n * min_val:
        min_val = total // n

    parts = weighted_split(total, weights)

    deficit = 0
    for i in range(n):
        if parts[i] < min_val:
            deficit += min_val - parts[i]
            parts[i] = min_val

    if deficit > 0:
        order = sorted(range(n), key=lambda i: -parts[i])
        ptr = 0
        while deficit > 0:
            j = order[ptr % n]
            spare = parts[j] - min_val
            if spare > 0:
                take = spare if spare < deficit else deficit
                parts[j] -= take
                deficit -= take
            ptr += 1
            if ptr > n * 4:  # 防御性保护，正常不会触发
                raise ValueError("金额拆分无法收敛，请检查 min_val 与 total")
    return parts


def _inject_big_deals(weights: List[float]) -> List[float]:
    """把权重最大的若干条放大成大单，模拟「头部大单贡献主要收入」的长尾结构。

    采用「目标营收占比」而非固定放大倍数：放大倍数会随分布形态漂移
    （top-k 的基数本身已远大于均值），而占比是可控且稳定的。

    放大后由 _split_with_min 重新归一化，总额不变，一致性校验依旧成立。
    """
    n = len(weights)
    k = max(1, int(n * BIG_DEAL_RATIO))
    if k >= n:
        return weights

    order = sorted(range(n), key=lambda i: -weights[i])[:k]
    total = sum(weights)
    cur = sum(weights[i] for i in order)
    if total <= 0 or cur <= 0:
        return weights

    target = min(max(BIG_DEAL_TARGET_SHARE, 0.0), 0.9)
    # 令 s*cur / (total - cur + s*cur) = target  =>  s = target*(total-cur) / (cur*(1-target))
    scale = target * (total - cur) / (cur * (1 - target))

    # 大单内部按排名递减，profile 归一化到均值 1，保证合计等于目标占比
    lo, hi = BIG_DEAL_PROFILE
    raw_profile = [hi - (hi - lo) * (r / max(1, k - 1)) for r in range(k)]
    mean_p = sum(raw_profile) / k
    for rank, i in enumerate(order):
        weights[i] *= scale * (raw_profile[rank] / mean_p)
    return weights


def _fmt(cents: int) -> str:
    v = int(cents)
    return f"{v // 100}.{v % 100:02d}"


def _land_date(rng: RNG, year: int, land_month: int) -> str:
    last_day = calendar.monthrange(year, land_month)[1]
    day = rng.randint(1, last_day)
    return f"{year}-{land_month:02d}-{day:02d}"


def generate_contracts(
    w: Any, rng: RNG, ctx: Dict[str, Any], plan: Dict[Tuple[str, int], Dict[str, Any]], scale: float = 1.0
) -> Dict[str, Any]:
    """生成商业市场台账，返回统计信息。"""
    w.table("fact_contract", COLUMNS)

    units = ctx["units"]
    products = ctx["products"]
    industries = ctx["industries"]
    customers = ctx["customers"]
    sales_by_unit = ctx["sales_by_unit"]
    product_weights = ctx["product_weights"]
    industry_weights = ctx["industry_weights"]

    # ── 1. 分配每个「单元 × 年度」的合同条数 ──────────────────────
    combos: List[Tuple[str, int]] = [(u["code"], y) for y in YEARS for u in units]
    combo_weights = [
        ctx["unit_by_code"][c]["size"] * (1.0 + YEAR_GROWTH[y]) for c, y in combos
    ]
    total_target = int(round(CONTRACT_TOTAL * scale))
    counts = weighted_split(total_target, combo_weights)

    seq = 0
    total_income_cents = 0
    risk_counter = {"低": 0, "中": 0, "高": 0}
    actual_income: Dict[Tuple[str, int], int] = {c: 0 for c in combos}

    for (unit_code, year), n in zip(combos, counts):
        if n <= 0:
            continue
        p = plan[(unit_code, year)]
        income_cents = p["income_cents"]
        if income_cents <= 0:
            continue

        # ── 2. 合同金额拆分（对数正态长尾）─────────────────────────
        raw_weights = [rng.lognormal(0.0, CONTRACT_AMOUNT_SIGMA) for _ in range(n)]
        raw_weights = _inject_big_deals(raw_weights)
        # 注意：放大权重后仍需走 _split_with_min —— 它会重新归一化，
        # 因此「Σ合同金额 = 计划收入」依旧严格成立。
        amounts = _split_with_min(income_cents, raw_weights, MIN_AMOUNT_CENTS)
        max_amount = max(amounts) if amounts else 1

        sales_pool = sales_by_unit[unit_code]
        unit_cust_cache: List[int] = []  # 制造「老客户复购」

        for amount_cents in amounts:
            seq += 1
            amount_wan = amount_cents / 100.0

            p_idx = rng.weighted_pick_index(product_weights[year])
            prod = products[p_idx]

            i_idx = rng.weighted_pick_index(industry_weights[year])
            ind = industries[i_idx]

            sale = sales_pool[rng.randint(0, len(sales_pool) - 1)]

            if unit_cust_cache and rng.bernoulli(0.6):
                cust = customers[unit_cust_cache[rng.randint(0, len(unit_cust_cache) - 1)]]
            else:
                ci = rng.randint(0, len(customers) - 1)
                cust = customers[ci]
                if len(unit_cust_cache) < 40:
                    unit_cust_cache.append(ci)

            land_month = rng.weighted_pick_index(MONTH_WEIGHTS) + 1
            land_date = _land_date(rng, year, land_month)

            # 台套数：由金额 / 单价推导，落在合理区间
            price = UNIT_PRICE[prod["type"]]
            qty = int(round(amount_wan / price))
            qty = max(QTY_RANGE[0], min(500, qty)) if qty > 0 else QTY_RANGE[0]

            amount_ex_tax = int(round(amount_cents / (1.0 + TAX_RATE)))
            margin = rng.uniform(*GROSS_MARGIN_RANGE)
            cost_cents = int(round(amount_cents * (1.0 - margin)))
            gross_profit_cents = amount_cents - cost_cents

            # ── 分月收入确认（落地月起 3 个月，跨年部分回流落地月）──
            idxs: List[int] = []
            wts: List[float] = []
            for k, wgt in enumerate(REVENUE_SPREAD):
                mi = land_month - 1 + k
                if mi <= 11:
                    idxs.append(mi)
                    wts.append(wgt)
            if not idxs:
                idxs = [land_month - 1]
                wts = [1.0]
            m_income = [0] * 12
            for mi, part in zip(idxs, weighted_split(amount_cents, wts)):
                m_income[mi] += part

            # ── 分月回款（滞后 1~3 个月，比例 ~ Beta(6,2)）──────────
            ratio = rng.beta(*PAYMENT_RATIO_BETA)
            pay_cents = int(round(amount_cents * ratio))
            lag = rng.randint(*PAYMENT_LAG_RANGE)
            pay_mi = min(11, land_month - 1 + lag)
            m_payment = [0] * 12
            m_payment[pay_mi] += pay_cents

            # ── 风险等级 ──────────────────────────────────────────
            p_risk = (
                RISK_BASE
                + RISK_AMOUNT_COEF * (amount_cents / max_amount)
                + RISK_MONTH_COEF * (land_month / 12.0)
            )
            p_risk = min(p_risk, RISK_MAX)
            if rng.bernoulli(p_risk):
                risk = "高"
            elif rng.bernoulli(0.35):
                risk = "中"
            else:
                risk = "低"
            risk_counter[risk] += 1

            q_income = [sum(m_income[i * 3:(i + 1) * 3]) for i in range(4)]
            q_payment = [sum(m_payment[i * 3:(i + 1) * 3]) for i in range(4)]
            year_income = sum(m_income)
            year_payment = sum(m_payment)

            remark = ""
            if rng.bernoulli(0.10):
                remark = f"备注{seq}"

            w.row(
                "fact_contract",
                [
                    f"HT-{year}-{seq:06d}",
                    f"YS-{year}-{seq:06d}",
                    f"OPP-{year}-{seq:06d}",
                    unit_code,
                    ind["code"],
                    prod["code"],
                    sale["id"],
                    cust["code"],
                    f"{cust['name']}{prod['model']}{PROJECT_NAME_SUFFIX[seq % 4]}",
                    qty,
                    _fmt(amount_cents),
                    _fmt(amount_ex_tax),
                    _fmt(cost_cents),
                    _fmt(gross_profit_cents),
                    land_date,
                    risk,
                    "true" if prod["is_new"] else "false",
                    "true" if rng.bernoulli(REBATE_RATE) else "false",
                    *[_fmt(v) for v in m_income],
                    *[_fmt(v) for v in q_income],
                    _fmt(year_income),
                    *[_fmt(v) for v in m_payment],
                    *[_fmt(v) for v in q_payment],
                    _fmt(year_payment),
                    remark,
                    year,
                ],
            )

            total_income_cents += year_income
            actual_income[(unit_code, year)] += year_income

    return {
        "contract_rows": seq,
        "total_income_cents": total_income_cents,
        "actual_income_by_unit_year": {f"{k[0]}|{k[1]}": v for k, v in actual_income.items()},
        "risk_distribution": risk_counter,
    }
