"""多轮对话测试（规则层，不需要数据库与模型）。"""

from __future__ import annotations

import pytest

from app.agent.nodes.intent import classify, needs_full_pipeline
from app.agent.nodes.result_ops import apply as apply_op
from app.agent.nodes.rewrite import extract_slots, rewrite
from app.agent.nodes.retrieve import SchemaContext
from app.agent.slots import Slots, merge

SCHEMA = SchemaContext(
    sources=[{"id": 1, "group_name": "台账数据", "name": "商业市场台账",
              "object_name": "bi.fact_contract", "object_type": "table", "description": ""}],
    metrics=[
        {"code": "biz_income", "name": "商业收入", "aliases": ["收入", "营收", "签约额"],
         "expr_sql": "SUM(f.year_income)", "unit": "万元", "agg_default": "SUM", "caliber": ""},
        {"code": "biz_payment", "name": "商业回款", "aliases": ["回款"],
         "expr_sql": "SUM(f.year_payment)", "unit": "万元", "agg_default": "SUM", "caliber": ""},
    ],
    dimensions=[
        {"code": "unit", "name": "经营单元", "aliases": ["办事处", "代表处", "单元"],
         "expr_sql": "f.unit_code", "display_expr": "d.unit_name",
         "join_sql": "LEFT JOIN bi.dim_unit AS d ON d.unit_code = f.unit_code", "dim_type": "categorical",
         "value_map": {"华北": ["北京代表处"], "华东": ["上海代表处", "浙江代表处"]}},
        {"code": "industry_cat", "name": "行业大类", "aliases": ["行业"],
         "expr_sql": "i.industry_cat", "display_expr": "i.industry_cat",
         "join_sql": "LEFT JOIN bi.dim_industry AS i ON i.industry_code = f.industry_code", "dim_type": "categorical",
         "value_map": ["政企", "运营商", "商业市场", "渠道部"]},
    ],
    rules=[],
    alias_hint="",
)


async def _rw(q, prev: Slots = None):
    return await rewrite(q, prev or Slots(), SCHEMA, default_year=2026)


# ── 指代消解 ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pronoun_inherits_time_and_metric():
    """Q1 各经营单元收入排名 → Q2「那北京呢」应继承 2026 与收入指标，主体切到北京"""
    prev = Slots(metrics=["biz_income"], dimensions=["unit"], time_range={"type": "year", "value": 2026})
    r = await _rw("那北京呢", prev)
    assert not r.need_clarify
    assert r.merged.subject == "北京代表处"
    assert r.merged.time_range == {"type": "year", "value": 2026}   # 继承
    assert r.merged.metrics == ["biz_income"]                        # 继承
    assert "2026" in r.rewritten and "北京代表处" in r.rewritten


@pytest.mark.asyncio
async def test_time_switch_inherits_subject():
    """Q2 北京 → Q3「同比呢」应保留北京，加入同比"""
    prev = Slots(metrics=["biz_income"], time_range={"type": "year", "value": 2026},
                 subject="北京代表处")
    r = await _rw("同比呢", prev)
    assert r.merged.compare == "yoy"
    assert r.merged.subject == "北京代表处"
    assert "同比" in r.rewritten


@pytest.mark.asyncio
async def test_condition_overlay():
    """Q3 → Q4「只看政企」应叠加筛选，且保留主体"""
    prev = Slots(metrics=["biz_income"], subject="北京代表处",
                 time_range={"type": "year", "value": 2026})
    r = await _rw("只看政企行业", prev)
    assert {"dim": "industry_cat", "op": "=", "value": "政企"} in r.merged.filters
    assert r.merged.subject == "北京代表处"


@pytest.mark.asyncio
async def test_self_contained_question_not_expanded():
    r = await _rw("2025年上海代表处的回款是多少", Slots(subject="北京代表处"))
    assert r.rewritten == "2025年上海代表处的回款是多少"
    assert r.merged.subject == "上海代表处"   # 本轮覆盖
    assert r.merged.time_range == {"type": "year", "value": 2025}


@pytest.mark.asyncio
async def test_clarify_when_nothing_known():
    r = await _rw("看看情况", Slots())
    assert r.need_clarify
    assert len(r.options) >= 3


# ── 槽位合并 ────────────────────────────────────────────────────────
def test_merge_filters_replaced_by_dim():
    prev = Slots(filters=[{"dim": "industry_cat", "op": "=", "value": "政企"}])
    cur = Slots(filters=[{"dim": "industry_cat", "op": "=", "value": "运营商"}])
    out = merge(prev, cur)
    assert out.filters == [{"dim": "industry_cat", "op": "=", "value": "运营商"}]


def test_merge_filters_accumulate():
    prev = Slots(filters=[{"dim": "industry_cat", "op": "=", "value": "政企"}])
    cur = Slots(filters=[{"dim": "unit", "op": "=", "value": "上海代表处"}])
    assert len(merge(prev, cur).filters) == 2


# ── 意图识别 ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "q,intent",
    [
        ("2026年各经营单元收入排名", "ranking"),
        ("各产品线收入占比", "proportion"),
        ("2026年每月的合同金额趋势", "trend"),
        ("2026年各经营单元收入同比", "compare"),
        ("高风险项目有哪些", "warning"),
        ("为什么北京代表处下降了", "attribution"),
        ("按收入降序排序", "result_ops"),
        ("换成饼图", "result_ops"),
        ("导出Excel", "result_ops"),
        ("你好", "chitchat"),
        ("今天天气怎么样", "out_of_scope"),
    ],
)
def test_intent(q, intent):
    assert classify(q).intent == intent


def test_intent_ops_detail():
    assert classify("按收入降序排序").op == "sort"
    assert classify("换成饼图").op == "chart"
    assert classify("导出Excel").op == "export"


def test_needs_full_pipeline():
    assert needs_full_pipeline(classify("各经营单元收入排名"))
    assert not needs_full_pipeline(classify("换成饼图"))
    assert not needs_full_pipeline(classify("你好"))


# ── 结果二次加工 ────────────────────────────────────────────────────
PAYLOAD = {
    "columns": ["经营单元", "商业收入"],
    "rows": [["上海代表处", 100.0], ["北京代表处", 300.0], ["浙江代表处", 200.0]],
    "total": 3,
    "truncated": False,
}


def test_ops_sort_desc():
    out, msg = apply_op(PAYLOAD, "sort", "按商业收入降序排序")
    assert [r[0] for r in out["rows"]] == ["北京代表处", "浙江代表处", "上海代表处"]
    assert "降序" in msg


def test_ops_sort_asc():
    out, _ = apply_op(PAYLOAD, "sort", "按商业收入升序")
    assert [r[0] for r in out["rows"]] == ["上海代表处", "浙江代表处", "北京代表处"]


def test_ops_topn():
    out, msg = apply_op(PAYLOAD, "topn", "只看前2个")
    assert len(out["rows"]) == 2
    assert "前 2" in msg


def test_ops_chart_pie():
    out, msg = apply_op(PAYLOAD, "chart", "换成饼图")
    assert out["chart"]["type"] == "pie"
    assert "饼图" in msg


def test_ops_chart_line():
    out, _ = apply_op(PAYLOAD, "chart", "用折线图")
    assert out["chart"]["type"] == "line"


# ── 槽位：条数不能被时间跨度的数字污染 ────────────────────────
# 回归：曾把「最近 3 个月」的 3 抽成 limit=3，给按月聚合的 SQL 加上 LIMIT 3，
# 砍掉最后一个月，导致「总收入」比「拆维度后合计」少一截。
@pytest.mark.parametrize("q", [
    "最近 3 个月的收入情况",
    "最近3个月收入",
    "前3个月的收入",
    "近6个月各代表处收入",
    "近12个月的收入",
])
def test_limit_not_taken_from_time_span(q):
    """时间跨度里的数字只进 time_range，不能同时被当成返回条数。"""
    s = extract_slots(q, SCHEMA)
    assert s.time_range, f"{q} 应识别出时间范围"
    assert s.limit is None, f"{q} 不应抽出 limit，实际为 {s.limit}"


@pytest.mark.parametrize("q,limit", [
    ("收入最高的前3个产品线", 3),
    ("TOP5 代表处", 5),
    ("前5名代表处的收入", 5),
])
def test_limit_still_extracted_for_topn(q, limit):
    """真正的 TOP N 语义不受影响。"""
    assert extract_slots(q, SCHEMA).limit == limit
