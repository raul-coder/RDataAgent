"""多轮对话测试（规则层，不需要数据库与模型）。"""

from __future__ import annotations

import pytest

from app.agent.nodes.intent import classify, needs_full_pipeline
from app.agent.nodes.result_ops import apply as apply_op
from app.agent.nodes.retrieve import SchemaContext
from app.agent.nodes.rewrite import extract_slots, rewrite
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
         # 与种子数据一致：「渠道部」既是经营单元，也是行业大类（见 industry_cat），
         # 这是构造「取值歧义」场景的真实来源
         "value_map": {"华北": ["北京代表处"], "华东": ["上海代表处", "浙江代表处"],
                       "渠道": ["渠道部"]}},
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


# ── 否定：规则层必须给出 != 而不是 =（无 LLM 时的保底）──────────────
# 这是最危险的一类抽取错误：SQL 能跑通、返回数据、数字看着也合理，
# 但统计的是用户明确排除掉的那一批。
@pytest.mark.parametrize("q", [
    "不含政企的收入", "不包含政企", "除了政企", "排除政企",
    "剔除政企", "不算政企", "不要政企", "不是政企", "非政企",
])
def test_negation_produces_not_equal(q):
    s = extract_slots(q, SCHEMA)
    assert s.filters, f"{q} 未抽出筛选条件"
    assert s.filters[0]["op"] == "!=", q
    assert s.filters[0]["value"] == "政企"


@pytest.mark.parametrize("q", ["不含 政企 的收入", "不含:政企"])
def test_negation_allows_small_gap(q):
    """否定词与取值之间允许少量间隔字符。"""
    assert extract_slots(q, SCHEMA).filters[0]["op"] == "!=", q


def test_negation_does_not_set_subject():
    """「不含北京」问的不是北京 —— 被排除的取值不能当分析主体。"""
    s = extract_slots("不含北京代表处的收入", SCHEMA)
    assert s.subject is None
    assert s.filters == [{"dim": "unit", "op": "!=", "value": "北京代表处"}]


def test_negation_only_applies_to_adjacent_value():
    """否定只修饰紧邻的取值，不扩散到后面的并列成分。"""
    s = extract_slots("不含政企、只看北京代表处的收入", SCHEMA)
    ops = {f["dim"]: f["op"] for f in s.filters}
    assert ops == {"industry_cat": "!=", "unit": "="}
    assert s.subject == "北京代表处"


@pytest.mark.parametrize("q", ["非常多的收入", "非洲市场的收入", "政企行业的收入"])
def test_no_false_negation(q):
    """「非常 / 非洲」不能被误判为否定词。"""
    s = extract_slots(q, SCHEMA)
    assert all(f["op"] == "=" for f in s.filters), q


# ── 取值歧义：同一取值命中多维度时只保留一个 ────────────────────────
def test_ambiguous_value_collapses_to_one_dimension():
    """「渠道部」既是经营单元又是行业 —— 不能同时加两个互相矛盾的筛选。

    两个筛选同时存在 = 查「既是渠道部行业又是渠道部单元」的数据，
    SQL 能跑通但结果几乎必然为空，属于最难察觉的一类错误。
    """
    s = extract_slots("渠道部的收入", SCHEMA)
    assert len(s.filters) == 1
    # 主体维度优先（unit 在 SUBJECT_DIM_CODES 里）
    assert s.filters[0]["dim"] == "unit"
    assert s.filters[0]["value"] == "渠道部"


def test_same_dim_multi_value_collapses_to_in():
    """同一维度多个取值必须合并为 in —— 否则 merge 时互相覆盖，只剩最后一个。

    注意：merge 侧的 in 并集只是「下游兼容」，规则层不产出 in 的话它就是死代码。
    """
    s = extract_slots("上海代表处和浙江代表处谁的收入更高", SCHEMA)
    dims = [f["dim"] for f in s.filters]
    assert dims.count("unit") == 1, f"unit 筛选应只有一条，实际 {s.filters}"
    unit = next(f for f in s.filters if f["dim"] == "unit")
    assert unit["op"] == "in"
    assert set(unit["value"]) == {"上海代表处", "浙江代表处"}


def test_subject_takes_longest_match():
    """多值命中时主体取最长匹配（5 字），不要被短的（3 字）覆盖。

    取值顺序按命中长度降序，「北京代表处」先于「渠道部」。
    """
    s = extract_slots("渠道部和北京代表处谁的收入更高", SCHEMA)
    assert s.subject == "北京代表处"
    unit = next(f for f in s.filters if f["dim"] == "unit")
    assert unit["op"] == "in"
    assert set(unit["value"]) == {"渠道部", "北京代表处"}


def test_negated_and_positive_values_stay_separate():
    """否定与肯定的取值不合并 —— op 不同，in 也无法表达取反。"""
    s = extract_slots("不含政企但包含运营商的收入", SCHEMA)
    pairs = {(f["dim"], f["op"]) for f in s.filters}
    assert ("industry_cat", "!=") in pairs, s.filters
    assert ("industry_cat", "=") in pairs, s.filters


@pytest.mark.asyncio
async def test_negation_survives_merge():
    """多轮追问下，否定条件要能随其它槽位一起继承。"""
    prev = Slots(metrics=["biz_income"], dimensions=["unit"])
    r = await _rw("不含政企", prev)
    assert {"dim": "industry_cat", "op": "!=", "value": "政企"} in r.merged.filters
    assert r.merged.metrics == ["biz_income"]      # 继承
    assert r.merged.dimensions == ["unit"]         # 继承


@pytest.mark.asyncio
async def test_rewrite_without_llm_still_produces_not_equal():
    """没有可用模型时，规则层也必须给出 != —— 这正是阶段 0 的意义。

    LLM 兜底是「增益」不是「依赖」：降级模式下不能退回反向语义。
    """
    r = await rewrite("不含政企的收入", Slots(), SCHEMA, providers=None)
    assert r.used_llm is False
    assert r.current.filters == [{"dim": "industry_cat", "op": "!=", "value": "政企"}]


@pytest.mark.asyncio
async def test_rewrite_without_llm_collapses_ambiguity():
    """降级模式下，歧义取值也不能退化成两个互相矛盾的筛选。"""
    r = await rewrite("渠道部的收入", Slots(), SCHEMA, providers=None)
    assert r.used_llm is False
    assert len(r.current.filters) == 1


def test_ambiguous_value_still_detectable_by_assess():
    """规则层收敛后，兜底层仍要能从问句识别出歧义并交给模型消解。

    这两层的分工：规则层保证「不会自相矛盾」，模型负责「猜对意图」。
    """
    from app.agent.nodes.slot_llm import assess

    s = extract_slots("渠道部的收入", SCHEMA)
    assert len(s.filters) == 1                       # 规则层已收敛
    a = assess("渠道部的收入", s, SCHEMA)
    assert any("歧义" in i for i in a.issues)        # 但兜底层知道这里有歧义
    assert {"filters", "subject"} <= a.suspicious


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
