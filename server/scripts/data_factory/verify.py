"""造数一致性校验。

直接在 CSV 产物上运行，不依赖数据库，可作为 CI 的质量闸门。
校验分为三层：
  L1 行内自洽：Σ分月 = Σ分季 = 年度
  L2 跨表自洽：Σ合同收入 = 计划收入；月度目标之和 = 年度目标
  L3 业务合理：完成率分布、风险占比、同比涨跌、参照完整性
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List, Tuple


class VerifyError(AssertionError):
    pass


def parse_cents(s: str) -> int:
    """'1234.56' → 123456（万元的百分之一），避免浮点误差。"""
    s = (s or "").strip()
    if not s:
        return 0
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    if "." in s:
        a, b = s.split(".", 1)
        b = (b + "00")[:2]
    else:
        a, b = s, "00"
    v = int(a or "0") * 100 + int(b)
    return -v if neg else v


def _read_csv(path: str) -> Tuple[List[str], List[List[str]]]:
    if not os.path.exists(path):
        raise VerifyError(f"缺少产物文件：{path}")
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    return header, rows


def _idx(header: List[str], *names: str) -> List[int]:
    out = []
    for n in names:
        if n not in header:
            raise VerifyError(f"CSV 缺少列：{n}（表头={header[:5]}...）")
        out.append(header.index(n))
    return out


def _cols(header: List[str], prefix: str, n: int) -> List[int]:
    return _idx(header, *[f"{prefix}{i}" for i in range(1, n + 1)])


def run(out_dir: str) -> List[str]:
    """执行全部校验，返回可读的检查结果列表；任何失败抛出 VerifyError。"""
    report: List[str] = []

    manifest_path = os.path.join(out_dir, "_manifest.json")
    if not os.path.exists(manifest_path):
        raise VerifyError("缺少 _manifest.json，请先执行造数")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    plan = manifest["plan"]  # {"BJ|2026": {"income_cents":..., "biz_goal_cents":...}}
    checks = 0

    # ── L1 合同行内自洽 ────────────────────────────────────────────
    h, rows = _read_csv(os.path.join(out_dir, "fact_contract.csv"))
    i_m_i = _cols_prefix(h, "m", "_income", 12)
    i_m_p = _cols_prefix(h, "m", "_payment", 12)
    i_q_i = _cols_prefix(h, "q", "_income", 4)
    i_q_p = _cols_prefix(h, "q", "_payment", 4)
    i_year_income = h.index("year_income")
    i_year_payment = h.index("year_payment")
    i_unit = h.index("unit_code")
    i_year = h.index("year")
    i_risk = h.index("risk_level")
    i_amount = h.index("amount")
    i_land = h.index("land_date")

    income_by_uy: Dict[Tuple[str, str], int] = {}
    risk_counter = {"低": 0, "中": 0, "高": 0}
    amount_sum = 0

    for r in rows:
        m_i = [parse_cents(r[i]) for i in i_m_i]
        m_p = [parse_cents(r[i]) for i in i_m_p]
        q_i = [parse_cents(r[i]) for i in i_q_i]
        q_p = [parse_cents(r[i]) for i in i_q_p]
        y_i = parse_cents(r[i_year_income])
        y_p = parse_cents(r[i_year_payment])

        if sum(m_i) != y_i:
            raise VerifyError(f"分月收入合计 {sum(m_i)} != 年度收入 {y_i}（{r[0]}）")
        if sum(m_p) != y_p:
            raise VerifyError(f"分月回款合计 {sum(m_p)} != 年度回款 {y_p}（{r[0]}）")
        for k in range(4):
            if sum(m_i[k * 3:(k + 1) * 3]) != q_i[k]:
                raise VerifyError(f"Q{k + 1} 收入 {q_i[k]} != 分月之和（{r[0]}）")
            if sum(m_p[k * 3:(k + 1) * 3]) != q_p[k]:
                raise VerifyError(f"Q{k + 1} 回款 {q_p[k]} != 分月之和（{r[0]}）")

        key = (r[i_unit], r[i_year])
        income_by_uy[key] = income_by_uy.get(key, 0) + y_i
        risk_counter[r[i_risk]] = risk_counter.get(r[i_risk], 0) + 1
        amount_sum += parse_cents(r[i_amount])
        checks += 4

    report.append(f"[L1] 合同行内自洽：{len(rows):,} 条 × 4 项 = {checks:,} 项断言通过")
    report.append(
        f"[L1] 合同总金额 {amount_sum / 100:,.0f} 万元，"
        f"总确认收入 {sum(income_by_uy.values()) / 100:,.0f} 万元"
    )

    # ── L2 Σ合同收入 = 计划收入 ────────────────────────────────────
    max_delta = 0
    for (unit, year), actual in sorted(income_by_uy.items()):
        expect = plan.get(f"{unit}|{year}", {}).get("income_cents")
        if expect is None:
            raise VerifyError(f"manifest 缺少计划收入：{unit}|{year}")
        delta = abs(actual - expect)
        max_delta = max(max_delta, delta)
        if delta > 1:
            raise VerifyError(
                f"{unit}|{year} 收入偏差 {delta} cents（实际 {actual} / 计划 {expect}）"
            )
    report.append(
        f"[L2] Σ合同收入 = 计划收入：{len(income_by_uy)} 个「单元×年度」全部命中，"
        f"最大偏差 {max_delta} cents"
    )

    # ── L2 目标台账：月度之和 = 年度 ───────────────────────────────
    h, rows = _read_csv(os.path.join(out_dir, "fact_goal.csv"))
    i_u, i_y, i_m = _idx(h, "unit_code", "year", "month")
    i_bg, i_sg = _idx(h, "biz_goal", "solution_goal")
    goal_agg: Dict[Tuple[str, str], List[int]] = {}
    for r in rows:
        key = (r[i_u], r[i_y])
        acc = goal_agg.setdefault(key, [0, 0, 0, 0])  # [月度biz, 年度biz, 月度sol, 年度sol]
        biz, sol = parse_cents(r[i_bg]), parse_cents(r[i_sg])
        if int(r[i_m]) == 0:
            acc[1] += biz
            acc[3] += sol
        else:
            acc[0] += biz
            acc[2] += sol
    for key, (mb, yb, ms, ys) in sorted(goal_agg.items()):
        if abs(mb - yb) > 1:
            raise VerifyError(f"{key} 商业目标月度之和 {mb} != 年度 {yb}")
        if abs(ms - ys) > 1:
            raise VerifyError(f"{key} 商解目标月度之和 {ms} != 年度 {ys}")
    report.append(f"[L2] 目标台账：{len(goal_agg)} 个「单元×年度」月度之和 = 年度，全部通过")

    # ── L3 业务合理性 ──────────────────────────────────────────────
    # 按年汇总目标与收入（plan 的 key 形如 "BJ|2026"）
    goal_by_year: Dict[str, int] = {}
    income_by_year: Dict[str, int] = {}
    for k, v in plan.items():
        year = k.split("|")[1]
        goal_by_year[year] = goal_by_year.get(year, 0) + v["biz_goal_cents"]
        income_by_year[year] = income_by_year.get(year, 0) + v["income_cents"]
    overall = sum(income_by_year.values()) / sum(goal_by_year.values())
    if not (0.30 <= overall <= 1.30):
        raise VerifyError(f"整体完成率 {overall:.3f} 超出合理区间 [0.30, 1.30]")
    report.append(f"[L3] 整体完成率 {overall * 100:.1f}%，落在合理区间")

    total_rows = sum(risk_counter.values())
    high_ratio = risk_counter["高"] / total_rows
    if not (0.08 <= high_ratio <= 0.18):
        raise VerifyError(f"高风险合同占比 {high_ratio:.3f} 超出目标区间 [0.08, 0.18]")
    report.append(
        f"[L3] 风险分布 低{risk_counter['低']:,} / 中{risk_counter['中']:,} / "
        f"高{risk_counter['高']:,}，高风险占比 {high_ratio * 100:.1f}%"
    )

    rates = {k: v["income_cents"] / v["biz_goal_cents"] for k, v in plan.items()}
    low_units = [k for k, r in rates.items() if r < 0.60]
    high_units = [k for k, r in rates.items() if r > 1.10]
    if not low_units:
        raise VerifyError("缺少低达成预警单元（完成率 < 60%）")
    if not high_units:
        raise VerifyError("缺少超额达成单元（完成率 > 110%）")
    report.append(
        f"[L3] 达成样本齐备：预警单元 {len(low_units)} 个（如 {low_units[:2]}），"
        f"超额单元 {len(high_units)} 个（如 {high_units[:2]}）"
    )

    if not (income_by_year.get("2026", 0) > income_by_year.get("2025", 0)):
        raise VerifyError("2026 年收入未高于 2025 年，同比增长故事不成立")
    yoy = income_by_year["2026"] / income_by_year["2025"] - 1
    report.append(
        f"[L3] 同比增长：2025 {income_by_year['2025'] / 100:,.0f} 万元 → "
        f"2026 {income_by_year['2026'] / 100:,.0f} 万元（{yoy * 100:+.1f}%）"
    )

    neg = []
    for (unit, year), _ in income_by_uy.items():
        if year != "2026":
            continue
        prev = plan.get(f"{unit}|2025")
        cur = plan.get(f"{unit}|2026")
        if prev and cur and cur["income_cents"] < prev["income_cents"]:
            neg.append(unit)
    if not neg:
        raise VerifyError("缺少同比下滑的经营单元，负增长分析素材不足")
    report.append(f"[L3] 同比下滑单元 {len(neg)} 个：{sorted(set(neg))}")

    # ── L3 参照完整性 ──────────────────────────────────────────────
    dims = {
        "unit_code": ("dim_unit.csv", "unit_code"),
        "industry_code": ("dim_industry.csv", "industry_code"),
        "product_code": ("dim_product.csv", "product_code"),
        "sales_id": ("dim_sales.csv", "sales_id"),
        "customer_code": ("dim_customer.csv", "customer_code"),
    }
    h, rows = _read_csv(os.path.join(out_dir, "fact_contract.csv"))
    for col, (fname, key) in dims.items():
        _, drows = _read_csv(os.path.join(out_dir, fname))
        kh = None
        valid = set()
        with open(os.path.join(out_dir, fname), "r", encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            header = next(rd)
            ki = header.index(key)
            for r in rd:
                valid.add(r[ki])
        ci = h.index(col)
        bad = {r[ci] for r in rows} - valid
        if bad:
            raise VerifyError(f"fact_contract.{col} 存在非法外键值：{list(bad)[:5]}")
        report.append(f"[L3] 参照完整性 fact_contract.{col} → {fname}：{len(valid)} 个合法值，无孤儿")

    # ── L3 PPL 合理性 ──────────────────────────────────────────────
    h, rows = _read_csv(os.path.join(out_dir, "fact_ppl.csv"))
    i_stage, i_risk2 = _idx(h, "stage", "risk_level")
    stage_counter: Dict[str, int] = {}
    ppl_risk: Dict[str, int] = {}
    for r in rows:
        stage_counter[r[i_stage]] = stage_counter.get(r[i_stage], 0) + 1
        ppl_risk[r[i_risk2]] = ppl_risk.get(r[i_risk2], 0) + 1
    if len(stage_counter) != 6:
        raise VerifyError(f"PPL 阶段分布不完整：{sorted(stage_counter)}")
    report.append(
        f"[L3] PPL {len(rows):,} 条，6 个阶段齐备，"
        f"风险分布 低{ppl_risk.get('低', 0):,} / 中{ppl_risk.get('中', 0):,} / 高{ppl_risk.get('高', 0):,}"
    )

    report.append(f"[OK] 全部校验通过（行内断言 {checks:,} 项）")
    return report


def _cols_prefix(header: List[str], prefix: str, suffix: str, n: int) -> List[int]:
    return _idx(header, *[f"{prefix}{i}{suffix}" for i in range(1, n + 1)])
