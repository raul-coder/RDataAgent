"""槽位抽取回归：黄金快照比对。

为什么需要它
────────────
`scripts/eval/run_eval.py` 要真实调用大模型，受配额与费用限制，
而槽位抽取（extract_slots）是**纯确定性逻辑**——同样的语义层 + 同样的问句，
输出必须逐字一致。用黄金快照比对可以在**零 LLM 调用**下锁住它的行为。

用法
────
    python -m scripts.check_slots_golden              # 回归：与基线比对
    python -m scripts.check_slots_golden --update     # 重新生成基线
    python -m scripts.check_slots_golden --verbose    # 显示全部差异明细

基线文件：`scripts/eval/slots_golden.json`（应随代码一起提交）。

何时需要 --update
────────────────
只在**有意为之**的行为变更后执行，例如新增同义词、修正抽取规则、
补充语义层指标/维度/取值。任何意外差异都应当被视为回归。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.agent.nodes.retrieve import SchemaContext, retrieve_schema
from app.db.session import readonly_session

EVAL_DIR = Path(__file__).parent / "eval"
CASES_PATH = EVAL_DIR / "cases.json"
GOLDEN_PATH = EVAL_DIR / "slots_golden.json"
DEFAULT_YEAR = 2026

#: 评测集未覆盖、但历史上出过问题的边界问法。
#: 每一行都是一次真实缺陷的回归守卫，删改前请先确认原因。
EDGE_CASES: list[str] = [
    # ── 否定：规则层曾把「不含政企」抽成 = 政企（语义相反）──
    "不含政企的经营单元收入",
    "不含 政企 的收入",
    "不含：政企",
    "除了政企之外的收入",
    "排除高风险的合同金额",
    # ── 否定不扩散：否定词只修饰紧邻取值 ──
    "不含政企、只看北京代表处的收入",
    "不含政企但包含运营商的收入",
    # ── 误伤守卫：「非常 / 非洲」不能被当成否定词 ──
    "非常多的收入",
    "非洲市场的收入",
    # ── 取值歧义：「渠道部」既是经营单元也是行业 ──
    "渠道部2026年的完成情况",
    "渠道部的收入",
    # ── 同维度多值：必须合并为 in，否则 merge 时互相覆盖 ──
    "上海代表处和浙江代表处谁的收入更高",
    "渠道部和北京代表处谁的收入更高",
    "PPL中高风险机会有哪些",
    "签约阶段的商机有多少个",
    # ── 多值合并后主体应取最长匹配 ──
    "北京和上海的收入",
]


async def build_schema() -> SchemaContext:
    async with readonly_session() as ro:
        return await retrieve_schema(ro)


def load_corpus() -> list[str]:
    questions = [c["question"] for c in json.load(open(CASES_PATH, encoding="utf-8"))]
    for q in EDGE_CASES:
        if q not in questions:
            questions.append(q)
    return questions


def extract_all(schema: SchemaContext, questions: list[str]) -> dict[str, dict]:
    from app.agent.nodes.rewrite import extract_slots

    return {
        q: extract_slots(q, schema, default_year=DEFAULT_YEAR).to_dict()
        for q in questions
    }


async def main() -> int:
    update = "--update" in sys.argv
    verbose = "--verbose" in sys.argv

    schema = await build_schema()
    questions = load_corpus()
    current = extract_all(schema, questions)

    if update:
        json.dump(current, open(GOLDEN_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"基线已更新：{len(current)} 条 -> {GOLDEN_PATH}")
        return 0

    if not GOLDEN_PATH.is_file():
        print(f"基线文件不存在，请先执行：python -m "
              f"{__name__} --update")
        return 1

    golden: dict[str, dict] = json.load(open(GOLDEN_PATH, encoding="utf-8"))

    # 语料可能增删（新增边界用例 / 评测集调整），分开统计
    only_golden = sorted(set(golden) - set(current))
    only_current = sorted(set(current) - set(golden))
    diffs = [q for q in current
             if q in golden and golden[q] != current[q]]

    print(f"语料 {len(current)} 条 · 基线 {len(golden)} 条")
    print("-" * 72)

    if only_golden:
        print(f"基线中已不存在的问句 {len(only_golden)} 条（语料缩减，不影响判定）")
    if only_current:
        print(f"新增问句 {len(only_current)} 条，需 --update 纳入基线：")
        for q in only_current[:10]:
            print(f"   + {q}")

    if not diffs:
        print("槽位抽取回归通过 ✅（与基线逐字一致）")
        return 0

    print(f"槽位抽取行为变化 {len(diffs)} 条：")
    for q in diffs:
        print(f"\n  ✗ {q}")
        if verbose:
            print(f"      基线 {_brief(golden[q])}")
            print(f"      当前 {_brief(current[q])}")
    print()
    print("若属有意为之，执行 --update 刷新基线；否则应视为回归。")
    return 1


def _brief(slots: dict) -> str:
    parts = []
    if slots.get("metrics"):
        parts.append(f"metrics={slots['metrics']}")
    if slots.get("dimensions"):
        parts.append(f"dims={slots['dimensions']}")
    if slots.get("filters"):
        parts.append(f"filters={slots['filters']}")
    if slots.get("subject"):
        parts.append(f"subject={slots['subject']!r}")
    if slots.get("time_range"):
        parts.append(f"time={slots['time_range']}")
    return " ".join(parts) or "(空)"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
