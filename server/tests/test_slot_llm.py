"""槽位 LLM 兜底（方案 A）测试。

覆盖三类行为，全部不需要数据库与真实模型：
  1. assess    —— 规则层自我评估（置信度 / 可疑信号），纯函数
  2. validate  —— 模型输出必须过语义层白名单
  3. combine   —— 规则优先、LLM 只补空缺与可疑字段

设计原则：LLM 兜底是**增益**而非**依赖**，因此「兜底不可用」与
「兜底结果非法」两条路径必须被测试锁住——否则某天兜底挂了会连带主链路。
"""

from __future__ import annotations

import json

import pytest

from app.agent.nodes import slot_llm
from app.agent.nodes.retrieve import SchemaContext
from app.agent.nodes.rewrite import rewrite
from app.agent.nodes.slot_llm import assess, combine, extract_slots_llm, validate
from app.agent.slots import Slots, merge
from app.llm.provider import LLMResponse
from app.llm.template_provider import TemplateProvider

SCHEMA = SchemaContext(
    sources=[{"id": 1, "group_name": "台账数据", "name": "商业市场台账",
              "object_name": "bi.fact_contract", "object_type": "table", "description": ""}],
    metrics=[
        {"code": "biz_income", "name": "商业收入", "aliases": ["收入", "营收"],
         "expr_sql": "SUM(f.year_income)", "unit": "万元", "agg_default": "SUM", "caliber": ""},
        {"code": "biz_payment", "name": "商业回款", "aliases": ["回款"],
         "expr_sql": "SUM(f.year_payment)", "unit": "万元", "agg_default": "SUM", "caliber": ""},
    ],
    dimensions=[
        {"code": "unit", "name": "经营单元", "aliases": ["办事处", "代表处"],
         "expr_sql": "f.unit_code", "display_expr": "d.unit_name",
         "join_sql": "LEFT JOIN bi.dim_unit AS d ON d.unit_code = f.unit_code",
         "dim_type": "categorical",
         # 注意「渠道部」同时是 industry_cat 的取值 —— 用于构造歧义场景
         "value_map": {"华北": ["北京代表处", "渠道部"], "华东": ["上海代表处", "浙江代表处"]}},
        {"code": "industry_cat", "name": "行业大类", "aliases": ["行业"],
         "expr_sql": "i.industry_cat", "display_expr": "i.industry_cat",
         "join_sql": "LEFT JOIN bi.dim_industry AS i ON i.industry_code = f.industry_code",
         "dim_type": "categorical",
         "value_map": ["政企", "运营商", "商业市场", "渠道部"]},
    ],
    rules=[],
    alias_hint="",
)


# ── assess：规则层的自我评估 ────────────────────────────────────────
def test_assess_empty_slots_needs_llm():
    """什么都没抽到 → 置信度 0，必须兜底。"""
    a = assess("今年卖得怎么样", Slots(), SCHEMA)
    assert a.confidence == 0.0
    assert a.needs_llm()
    assert "未识别到指标/维度/主体" in a.issues


def test_assess_confident_extraction_no_llm():
    """指标 + 维度都命中且无异常信号 → 规则结果够用，不浪费调用。"""
    s = Slots(metrics=["biz_income"], dimensions=["unit"])
    a = assess("各经营单元收入", s, SCHEMA)
    assert not a.issues
    assert not a.needs_llm(threshold=0.6)
    assert a.confidence == pytest.approx(0.7)


def test_assess_negation_is_suspicious():
    """否定词：规则层会把「不含政企」匹配成 industry_cat = 政企（语义相反）。"""
    s = Slots(metrics=["biz_income"], filters=[{"dim": "industry_cat", "op": "=", "value": "政企"}])
    a = assess("不含政企的收入", s, SCHEMA)
    assert any("否定" in i for i in a.issues)
    assert {"filters", "subject"} <= a.suspicious
    assert a.needs_llm()


def test_assess_range_is_suspicious():
    """数值区间：槽位结构（= / != / in）表达不了，必须交给模型避免错抽。"""
    s = Slots(metrics=["biz_income"], dimensions=["unit"])
    a = assess("收入3000万到5000万的经营单元", s, SCHEMA)
    assert any("区间" in i for i in a.issues)
    assert "filters" in a.suspicious


@pytest.mark.parametrize("q", ["收入超过5000万的经营单元", "完成率低于60%的单元"])
def test_assess_comparison_is_suspicious(q):
    a = assess(q, Slots(metrics=["biz_income"]), SCHEMA)
    assert any("区间" in i for i in a.issues)


def test_assess_value_ambiguity():
    """「渠道部」既是经营单元又是行业 —— 规则层同时命中两个维度，属歧义。"""
    s = Slots(
        metrics=["biz_income"],
        filters=[{"dim": "unit", "op": "=", "value": "渠道部"},
                 {"dim": "industry_cat", "op": "=", "value": "渠道部"}],
    )
    a = assess("渠道部的收入", s, SCHEMA)
    assert any("歧义" in i for i in a.issues)
    assert {"filters", "subject"} <= a.suspicious


def test_assess_multi_value_same_dim():
    """同维度多值会被 merge 互相覆盖 —— 需要模型给出 in 条件。"""
    s = Slots(
        metrics=["biz_income"],
        filters=[{"dim": "unit", "op": "=", "value": "北京代表处"},
                 {"dim": "unit", "op": "=", "value": "上海代表处"}],
    )
    a = assess("北京和上海的收入", s, SCHEMA)
    assert any("多值" in i for i in a.issues)
    assert "filters" in a.suspicious


# ── combine：规则优先，LLM 只补空缺与可疑字段 ───────────────────────
def test_combine_rule_wins_on_normal_fields():
    """非可疑字段：规则命中是高置信字面匹配，不许模型改坏。"""
    rule = Slots(metrics=["biz_income"], dimensions=["unit"])
    llm = Slots(metrics=["biz_payment"], dimensions=["industry_cat"])
    out = combine(rule, llm, suspicious=set())
    assert out.metrics == ["biz_income"]
    assert out.dimensions == ["unit"]


def test_combine_llm_fills_gaps():
    """规则没抽到的字段由模型补上。"""
    rule = Slots(dimensions=["unit"])
    llm = Slots(metrics=["biz_income"], time_range={"type": "year", "value": 2025})
    out = combine(rule, llm, suspicious=set())
    assert out.metrics == ["biz_income"]
    assert out.time_range == {"type": "year", "value": 2025}


def test_combine_llm_overrides_suspicious_only():
    """可疑字段允许被覆盖：这里是规则把「不含政企」抽反了的修复路径。"""
    rule = Slots(filters=[{"dim": "industry_cat", "op": "=", "value": "政企"}])
    llm = Slots(filters=[{"dim": "industry_cat", "op": "!=", "value": "政企"}])
    out = combine(rule, llm, suspicious={"filters"})
    assert out.filters == [{"dim": "industry_cat", "op": "!=", "value": "政企"}]


def test_combine_suspicious_but_llm_empty_keeps_rule():
    """可疑但模型也没给出更好答案时，仍沿用规则结果（不能把字段清空）。"""
    rule = Slots(metrics=["biz_income"])
    out = combine(rule, Slots(), suspicious={"metrics"})
    assert out.metrics == ["biz_income"]


# ── validate：模型输出必须过语义层白名单 ────────────────────────────
def test_validate_drops_unknown_metric_and_dimension():
    out = validate({"metrics": ["biz_income", "幻觉指标"],
                    "dimensions": ["unit", "幻觉维度"]}, SCHEMA)
    assert out is not None
    assert out.metrics == ["biz_income"]
    assert out.dimensions == ["unit"]


def test_validate_drops_value_not_in_value_map():
    out = validate({"filters": [
        {"dim": "industry_cat", "op": "=", "value": "火星行业"},
        {"dim": "industry_cat", "op": "=", "value": "政企"},
    ]}, SCHEMA)
    assert out is not None
    assert out.filters == [{"dim": "industry_cat", "op": "=", "value": "政企"}]


def test_validate_keeps_negation_and_multi_value():
    out = validate({"filters": [
        {"dim": "industry_cat", "op": "!=", "value": "政企"},
        {"dim": "unit", "op": "in", "value": ["北京代表处", "上海代表处", "火星"]},
    ]}, SCHEMA)
    assert out is not None
    assert out.filters[0] == {"dim": "industry_cat", "op": "!=", "value": "政企"}
    assert out.filters[1]["op"] == "in"
    assert out.filters[1]["value"] == ["北京代表处", "上海代表处"]


def test_validate_drops_illegal_op_and_subject():
    out = validate({
        "filters": [{"dim": "unit", "op": ">", "value": "北京代表处"}],
        "subject": "火星代表处",
    }, SCHEMA)
    assert out is None  # 全被丢弃 → 等价于模型没帮上忙


def test_validate_subject_must_be_known():
    assert validate({"subject": "北京代表处"}, SCHEMA) is not None
    assert validate({"subject": "不存在的单元"}, SCHEMA) is None


@pytest.mark.parametrize("year", [3026, 1999, 0])
def test_validate_rejects_implausible_year(year):
    out = validate({"time_range": {"type": "year", "value": year}}, SCHEMA)
    assert out is None


def test_validate_normalises_order_and_limit():
    out = validate({
        "order": {"by": "biz_income", "dir": "乱写"},
        "limit": 99999,
    }, SCHEMA)
    assert out is not None
    assert out.order == {"by": "biz_income", "dir": "desc"}  # 非法方向回落到 desc
    assert out.limit == 1000                                  # 超过上限被夹住


def test_validate_order_by_must_be_known_metric():
    assert validate({"order": {"by": "幻觉指标", "dir": "desc"}}, SCHEMA) is None


def test_validate_compare_whitelist():
    assert validate({"compare": "yoy"}, SCHEMA) is not None
    assert validate({"compare": "随便写的"}, SCHEMA) is None


# ── merge：多值筛选取并集 ───────────────────────────────────────────
def test_merge_in_filters_union():
    prev = Slots(filters=[{"dim": "unit", "op": "in", "value": ["北京代表处"]}])
    cur = Slots(filters=[{"dim": "unit", "op": "in", "value": ["上海代表处", "北京代表处"]}])
    out = merge(prev, cur)
    assert len(out.filters) == 1
    # 去重且保持顺序
    assert out.filters[0]["value"] == ["北京代表处", "上海代表处"]


def test_merge_in_and_eq_do_not_collapse():
    """op 不同视为不同条件，不应互相覆盖。"""
    prev = Slots(filters=[{"dim": "industry_cat", "op": "=", "value": "政企"}])
    cur = Slots(filters=[{"dim": "industry_cat", "op": "!=", "value": "运营商"}])
    out = merge(prev, cur)
    assert len(out.filters) == 2


# ── 接线：兜底真的接上了主链路 ──────────────────────────────────────
class _StubProvider:
    """只满足 LLMProvider 协议的最小实现，避免测试依赖真实模型。"""

    is_remote = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def chat(self, messages, *, question=None, temperature=0.1,
                   max_tokens=2048, json_mode=False, model=None):
        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False), model="stub"
        )


@pytest.mark.asyncio
async def test_extract_slots_llm_end_to_end():
    """规则层抽不到时，模型输出过白名单后被采纳。"""
    payload = {"metrics": ["biz_income", "幻觉指标"], "dimensions": ["unit"]}
    slots = await extract_slots_llm("今年卖得怎么样", SCHEMA, [_StubProvider(payload)], 2026)
    assert slots is not None
    assert slots.metrics == ["biz_income"]   # 幻觉指标被白名单剔除
    assert slots.dimensions == ["unit"]


@pytest.mark.asyncio
async def test_extract_slots_llm_skips_non_remote_provider():
    """检索式兜底 Provider 只能产出 SQL，不能拿它填槽位。"""
    assert await extract_slots_llm("x", SCHEMA, [TemplateProvider([])], 2026) is None
    assert await extract_slots_llm("x", SCHEMA, None, 2026) is None


@pytest.mark.asyncio
async def test_extract_slots_llm_disabled(monkeypatch):
    """关闭开关后行为与改造前一致（纯规则）。"""
    monkeypatch.setattr(slot_llm.settings, "SLOT_LLM_ENABLED", False)
    assert await extract_slots_llm("x", SCHEMA, [_StubProvider({})], 2026) is None


@pytest.mark.asyncio
async def test_extract_slots_llm_discards_all_invalid_output():
    """模型输出全非法 → 视为没帮上忙，返回 None（而不是塞进空槽位）。"""
    slots = await extract_slots_llm(
        "x", SCHEMA, [_StubProvider({"metrics": ["幻觉指标"]})], 2026
    )
    assert slots is None


@pytest.mark.asyncio
async def test_rewrite_uses_llm_when_rules_fail():
    """规则层抽不到指标时用模型补上，而不是只能澄清反问。"""
    payload = {
        "metrics": ["biz_income"],
        "dimensions": ["unit"],
        "time_range": {"type": "year", "value": 2026},
    }
    r = await rewrite("今年卖得怎么样", Slots(), SCHEMA, providers=[_StubProvider(payload)])
    assert r.used_llm is True
    assert not r.need_clarify
    assert r.merged.metrics == ["biz_income"]
    assert r.merged.time_range == {"type": "year", "value": 2026}


@pytest.mark.asyncio
async def test_rewrite_without_providers_keeps_old_behaviour():
    """无可用模型时退回纯规则，行为与改造前一致。"""
    # 完全抽不到任何东西 → 澄清（改造前就是这样）
    r = await rewrite("看看情况", Slots(), SCHEMA, providers=None)
    assert r.used_llm is False
    assert r.need_clarify
    assert r.reason == "未能识别要分析的指标或对象"

    # 只抽到时间、抽不到指标时，规则层**不会**澄清（is_empty 只看有没有任意槽位），
    # 而是带着一个残缺的槽位继续往下走 —— 这正是方案 A 要补的缺口。
    r2 = await rewrite("今年卖得怎么样", Slots(), SCHEMA, providers=None)
    assert r2.used_llm is False
    assert not r2.need_clarify
    assert r2.merged.metrics == []
    assert r2.merged.time_range == {"type": "year", "value": 2026}


@pytest.mark.asyncio
async def test_rewrite_llm_fixes_reversed_negation():
    """规则层把「不含政企」抽成了 = 政企，模型纠正为 != 政企。"""
    payload = {"filters": [{"dim": "industry_cat", "op": "!=", "value": "政企"}]}
    r = await rewrite("不含政企的收入", Slots(), SCHEMA, providers=[_StubProvider(payload)])
    assert r.used_llm is True
    assert r.current.filters == [{"dim": "industry_cat", "op": "!=", "value": "政企"}]
