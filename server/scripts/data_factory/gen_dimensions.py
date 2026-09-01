"""维度表生成：经营单元 / 行业 / 产品 / 销售 / 客户 / 日期。"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Sequence, Tuple

from .config import (
    CUSTOMER_CITIES,
    CUSTOMER_COUNT,
    CUSTOMER_LEVEL_WEIGHT,
    CUSTOMER_SUFFIX,
    CUSTOMER_WORDS,
    INDUSTRY_CATS,
    INDUSTRY_SUBS,
    INDUSTRY_SUB_YEAR_BOOST,
    MANAGER_POOL,
    PRODUCTS,
    PRODUCT_LINE_WEIGHT,
    SALES_COUNT,
    SALES_GIVEN,
    SALES_SURNAMES,
    UNITS,
    YEARS,
)
from .rng import RNG


def generate_dimensions(w: Any, rng: RNG) -> Dict[str, Any]:
    """生成全部维度表，并返回后续生成器需要的维度上下文。"""
    # ── 经营单元 ────────────────────────────────────────────────────
    w.table("dim_unit", ["unit_code", "unit_name", "region", "is_key_unit", "manager"])
    units: List[Dict[str, Any]] = []
    for i, (code, name, region, size, is_key) in enumerate(UNITS):
        manager = MANAGER_POOL[i % len(MANAGER_POOL)]
        w.row("dim_unit", [code, name, region, "true" if is_key else "false", manager])
        units.append(
            {
                "code": code,
                "name": name,
                "region": region,
                "size": size,
                "is_key": is_key,
                "manager": manager,
            }
        )

    # ── 行业（大类 × 小类 = 16）─────────────────────────────────────
    w.table("dim_industry", ["industry_code", "industry_cat", "industry_sub"])
    industries: List[Dict[str, Any]] = []
    idx = 0
    for cat, cat_w in INDUSTRY_CATS:
        for sub, sub_w in INDUSTRY_SUBS:
            idx += 1
            code = f"I{idx:02d}"
            w.row("dim_industry", [code, cat, sub])
            industries.append({"code": code, "cat": cat, "sub": sub, "base_w": cat_w * sub_w})

    # 分年度行业权重（智能制造在 2026 年爆发）
    industry_weights: Dict[int, List[float]] = {}
    for year in YEARS:
        ws = []
        for ind in industries:
            boost = INDUSTRY_SUB_YEAR_BOOST.get((ind["sub"], year), 1.0)
            ws.append(ind["base_w"] * boost)
        industry_weights[year] = ws

    # ── 产品（30 个）────────────────────────────────────────────────
    w.table(
        "dim_product",
        ["product_code", "product_line", "product_type", "model", "new_model", "is_new"],
    )
    products: List[Dict[str, Any]] = []
    for i, (line, ptype, model, is_new) in enumerate(PRODUCTS, start=1):
        code = f"P{i:03d}"
        new_model = f"{model}-N" if is_new else ""
        w.row("dim_product", [code, line, ptype, model, new_model, "true" if is_new else "false"])
        products.append(
            {"code": code, "line": line, "type": ptype, "model": model, "is_new": is_new}
        )

    # 分年度产品权重：产品线权重 / 该线型号数，新型号打 0.7 折
    product_weights: Dict[int, List[float]] = {}
    for year in YEARS:
        line_w = PRODUCT_LINE_WEIGHT[year]
        line_counts: Dict[str, int] = {}
        for p in products:
            line_counts[p["line"]] = line_counts.get(p["line"], 0) + 1
        ws = []
        for p in products:
            base = line_w[p["line"]] / line_counts[p["line"]]
            ws.append(base * (0.7 if p["is_new"] else 1.0))
        product_weights[year] = ws

    # ── 销售（按单元体量分配，每单元至少 2 人）─────────────────────
    w.table("dim_sales", ["sales_id", "sales_name", "unit_code", "role"])
    sales: List[Dict[str, Any]] = []
    sales_by_unit: Dict[str, List[Dict[str, Any]]] = {u["code"]: [] for u in units}
    total_size = sum(u["size"] for u in units)
    alloc: List[Tuple[Dict[str, Any], int]] = []
    remaining = SALES_COUNT
    for i, u in enumerate(units):
        if i == len(units) - 1:
            n = max(2, remaining)
        else:
            n = max(2, int(round(SALES_COUNT * u["size"] / total_size)))
            n = min(n, remaining - 2 * (len(units) - i - 1))
        alloc.append((u, n))
        remaining -= n
    sid = 0
    for u, n in alloc:
        for _ in range(n):
            sid += 1
            surname = SALES_SURNAMES[(sid * 7) % len(SALES_SURNAMES)]
            given = SALES_GIVEN[(sid * 11) % len(SALES_GIVEN)]
            name = f"{surname}{given}"
            role = "行销" if sid % 4 == 0 else "销售"
            code = f"S{sid:03d}"
            w.row("dim_sales", [code, name, u["code"], role])
            rec = {"id": code, "name": name, "unit": u["code"], "role": role}
            sales.append(rec)
            sales_by_unit[u["code"]].append(rec)

    # ── 客户（200 家，组合唯一）─────────────────────────────────────
    w.table("dim_customer", ["customer_code", "customer_name", "industry_code", "customer_level"])
    customers: List[Dict[str, Any]] = []
    ind_codes = [i["code"] for i in industries]
    ind_base_w = [i["base_w"] for i in industries]
    for i in range(CUSTOMER_COUNT):
        city = CUSTOMER_CITIES[(i // 90) % len(CUSTOMER_CITIES)]
        word = CUSTOMER_WORDS[(i // 6) % len(CUSTOMER_WORDS)]
        suffix = CUSTOMER_SUFFIX[i % len(CUSTOMER_SUFFIX)]
        name = f"{city}{word}{suffix}"
        code = f"C{i + 1:04d}"
        ind_code = ind_codes[rng.weighted_pick_index(ind_base_w)]
        level = rng.weighted_choice(
            list(CUSTOMER_LEVEL_WEIGHT.keys()), list(CUSTOMER_LEVEL_WEIGHT.values())
        )
        w.row("dim_customer", [code, name, ind_code, level])
        customers.append({"code": code, "name": name, "industry": ind_code, "level": level})

    # ── 日期（2025-01-01 ~ 2026-12-31）─────────────────────────────
    w.table("dim_date", ["d", "year", "quarter", "month", "year_month", "year_quarter"])
    d = dt.date(2025, 1, 1)
    end = dt.date(2026, 12, 31)
    while d <= end:
        w.row(
            "dim_date",
            [
                d.isoformat(),
                d.year,
                (d.month - 1) // 3 + 1,
                d.month,
                f"{d.year}-{d.month:02d}",
                f"{d.year}-Q{(d.month - 1) // 3 + 1}",
            ],
        )
        d += dt.timedelta(days=1)

    return {
        "units": units,
        "unit_by_code": {u["code"]: u for u in units},
        "industries": industries,
        "industry_weights": industry_weights,
        "products": products,
        "product_weights": product_weights,
        "sales": sales,
        "sales_by_unit": sales_by_unit,
        "customers": customers,
    }
