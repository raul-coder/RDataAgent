"""上下文改写：把「那北京呢」「同比呢」「只看政企」补全成可独立执行的完整问题。

策略（规则优先，保证低延迟；规则判不定时交给轻量模型）：
    ① 从当前问题中抽取槽位（指标 / 维度 / 主体 / 时间 / 对比 / 条数 / 筛选）
       —— 规则字面匹配，规则拿不准时由 slot_llm 用轻量模型补一次
    ② 识别续问信号（那/它/这个/上面/再/还/其它/呢 等指代词）
    ③ 本轮为空的槽位继承上文的 active_slots —— 这就是「指代消解 + 条件叠加」
    ④ 仍无法确定分析对象时，主动澄清反问（给出候选，绝不臆测）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ...core.logging import get_logger, log_kv
from ..slots import (
    NEGATION_MARKERS,
    NEGATION_WEAK_RE,
    OP_LABELS,
    Slots,
    filter_value_text,
    flatten_values,
    match_hits,
    merge,
)
from . import slot_llm
from .retrieve import SchemaContext

logger = get_logger(__name__)

# 续问信号
CONTINUATION_MARKERS = ("那", "它", "这个", "上面", "再", "还", "其它", "其他", "其余", "呢", "换成", "改成", "反过来")

# 只有这些维度的取值才被视作「分析主体」（其余维度取值只作筛选）
SUBJECT_DIM_CODES = frozenset({"unit", "region", "customer", "sales", "product_line"})
# 结果二次加工信号（不改 SQL，只改呈现）
RESULT_OPS_MARKERS = ("排序", "升序", "降序", "倒序", "换成", "改成", "改一下", "用饼图", "用柱状", "用折线", "画图", "图表", "导出", "下载", "翻页", "下一页", "多显示")

TIME_PATTERNS = (
    (re.compile(r"(20\d{2})\s*年"), "year"),
    (re.compile(r"今年|本年"), "this_year"),
    (re.compile(r"去年|上一年"), "last_year"),
    (re.compile(r"前年"), "year_before_last"),
    (re.compile(r"上季度"), "last_quarter"),
    (re.compile(r"本季度|这季度"), "this_quarter"),
    (re.compile(r"(?:近|前|过去)\s*(\d+)\s*个?\s*月"), "last_months"),
)

# 时间跨度片段，如「最近3个月」「前6个月」「过去2年」。
# 抽条数时必须先从问题中剔除：否则「最近 3 个月」里的 3 会被当成返回条数，
# 给聚合 SQL 加上 LIMIT 3，把分组结果截断 ——
# 表现为「总收入」与「拆维度后合计」两个数字对不上。
TIME_SPAN_RE = re.compile(
    r"(?:最近|近|前|过去|上)\s*\d+\s*(?:个\s*)?(?:月|年|季|季度|周|天|日)"
)

LIMIT_PATTERNS = (
    re.compile(r"(?:TOP|top)\s*(\d+)"),
    re.compile(r"前\s*(\d+)\s*(?:个|名|条)?"),
    # 「3个产品线」算条数，「3个月」不算
    re.compile(r"(\d+)\s*个(?!\s*(?:月|年|季|季度|周|天|日))"),
)


@dataclass
class RewriteResult:
    rewritten: str
    current: Slots = field(default_factory=Slots)
    merged: Slots = field(default_factory=Slots)
    need_clarify: bool = False
    options: list[str] = field(default_factory=list)
    reason: str = ""
    used_llm: bool = False


# ── 否定识别 ────────────────────────────────────────────────────────
# 规则层原本把「不含政企」匹配成 industry_cat = 政企 —— **语义完全相反**。
# 这是最危险的一类抽取错误：SQL 能正常跑通、返回数据、数字看着也合理，
# 但统计的是被用户明确排除掉的那一批。
#
# 判定采用「否定词紧邻取值」的位置规则：只有否定词出现在取值之前
# 且间隔不超过 NEGATION_WINDOW 个字符时才算否定。这样「不含政企、只看北京」
# 里的「北京」不会被误判为排除。
#: 否定词与取值之间允许的字符间隔（容纳「不含 政企」「不含：政企」这类写法）
NEGATION_WINDOW = 2
_MAX_NEGATION_LEN = max(len(m) for m in NEGATION_MARKERS)


def is_negated(question: str, key: str) -> bool:
    """该取值在问句中是否被否定词修饰（紧邻其左侧）。"""
    idx = (question or "").find(key)
    if idx < 0:
        return False
    start = max(0, idx - _MAX_NEGATION_LEN - NEGATION_WINDOW)
    prefix = question[start:idx]

    for m in NEGATION_MARKERS:
        p = prefix.rfind(m)
        # 否定词结尾到取值开头的距离
        if p >= 0 and 0 <= idx - (start + p + len(m)) <= NEGATION_WINDOW:
            return True
    # 单字「非」走正则，避免误伤「非常 / 非凡 / 非法」
    for m in NEGATION_WEAK_RE.finditer(prefix):
        if 0 <= idx - (start + m.end()) <= NEGATION_WINDOW:
            return True
    return False


def _resolve_ambiguity(
    candidates: list[tuple[str, str, bool]], schema: SchemaContext
) -> list[tuple[str, str, bool]]:
    """收敛取值歧义：同一取值命中多个维度时只保留一个。

    为什么不能都留：「渠道部」既是经营单元也是行业，两个筛选同时加上
    等于查「既是渠道部行业、又是渠道部单元」的数据——几乎必然是错的，
    而且错得悄无声息（SQL 能跑通、只是结果为空或偏少）。

    这里给出的是**确定性兜底**：主体维度优先，其次按语义层的维度顺序。
    它不追求猜对——猜对是 LLM 兜底层的职责（assess 会把歧义标记为
    suspicious，让模型来选）。它的价值在于**没有模型时也不会给出自相矛盾的 SQL**。
    """
    by_value: dict[str, list[tuple[str, bool]]] = {}
    for dim_code, value, negated in candidates:
        by_value.setdefault(value, []).append((dim_code, negated))

    dim_rank = {str(d["code"]): i for i, d in enumerate(schema.dimensions)}
    out: list[tuple[str, str, bool]] = []
    for value, hits in by_value.items():
        if len(hits) == 1:
            dim_code, negated = hits[0]
            out.append((dim_code, value, negated))
            continue
        # 主体维度优先（SUBJECT_DIM_CODES），其次按语义层维度顺序
        chosen = min(
            hits,
            key=lambda h: (h[0] not in SUBJECT_DIM_CODES, dim_rank.get(h[0], 999)),
        )
        out.append((chosen[0], value, chosen[1]))
    return out


def extract_slots(
    question: str, schema: SchemaContext, default_year: int = 2026
) -> Slots:
    """从问题中抽取槽位（基于语义层的别名与取值表）。"""
    q = question or ""
    s = Slots()

    # 指标（含别名）
    for m in schema.metrics:
        names = [m["name"], m["code"], *(m.get("aliases") or [])]
        if any(n and n in q for n in names):
            s.metrics.append(m["code"])
    # 维度（含别名）
    for d in schema.dimensions:
        names = [d["name"], d["code"], *(d.get("aliases") or [])]
        if any(n and n in q for n in names):
            s.dimensions.append(d["code"])

    # 主体 / 筛选：命中维度取值表
    # 只有「主实体」维度（经营单元、区域、客户、销售）的取值才构成分析主体；
    # 行业、产品线等维度取值一律只作为筛选条件叠加，避免覆盖上文主体。
    #
    # 先收集全部候选再统一落槽位：同一个取值可能命中多个维度（取值歧义），
    # 必须在这里收敛成一个，否则会同时加上两个互相矛盾的筛选条件。
    candidates: list[tuple[str, str, bool]] = []   # (dim_code, value, negated)
    for d in schema.dimensions:
        # 时间由 time_range 单独建模，再生成 year 筛选会与「2026年」重复
        if d["code"] == "year":
            continue
        for key, v in match_hits(q, flatten_values(d.get("value_map"))):
            candidates.append((d["code"], v, is_negated(q, key)))

    # 同一维度的多个取值合并成 in —— 否则 merge 时会互相覆盖，只剩最后一个。
    # （merge 侧的 in 并集是给 LLM 兜底输出用的；规则层必须自己产出 in。）
    grouped: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for dim_code, value, negated in _resolve_ambiguity(candidates, schema):
        key = (dim_code, "!=" if negated else "=")
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(value)

    for dim_code, op in order:
        values = grouped[(dim_code, op)]
        # 多值且非否定 → in；否定多值保持独立（in 无法表达取反，交给 SQL 生成）
        if len(values) > 1 and op == "=":
            s.filters.append({"dim": dim_code, "op": "in", "value": values})
        else:
            for v in values:
                s.filters.append({"dim": dim_code, "op": op, "value": v})
        # 被排除的取值不能当分析主体——「不含北京」问的不是北京。
        # 取第一个（match_hits 按命中长度降序，即最长匹配），不要用最后一个覆盖。
        if op == "=" and dim_code in SUBJECT_DIM_CODES and s.subject is None:
            s.subject = values[0]

    # 时间
    for pat, kind in TIME_PATTERNS:
        m = pat.search(q)
        if not m:
            continue
        if kind == "year":
            s.time_range = {"type": "year", "value": int(m.group(1))}
        elif kind == "this_year":
            s.time_range = {"type": "year", "value": default_year}
        elif kind == "last_year":
            s.time_range = {"type": "year", "value": default_year - 1}
        elif kind == "year_before_last":
            s.time_range = {"type": "year", "value": default_year - 2}
        elif kind == "last_quarter":
            s.time_range = {"type": "quarter", "value": f"{default_year}-Q3"}
        elif kind == "this_quarter":
            s.time_range = {"type": "quarter", "value": f"{default_year}-Q4"}
        elif kind == "last_months":
            s.time_range = {"type": "last_months", "value": int(m.group(1))}
        break

    # 对比
    if re.search(r"同比|同期|增长|增速|yoy", q, re.I):
        s.compare = "yoy"
    elif re.search(r"环比", q):
        s.compare = "mom"

    # 条数：时间已由 time_range 单独建模，先剔除时间片段再抽，
    # 避免「最近 3 个月」的 3 被误当成条数（见 TIME_SPAN_RE 注释）。
    q_for_limit = TIME_SPAN_RE.sub("", q)
    for pat in LIMIT_PATTERNS:
        m = pat.search(q_for_limit)
        if m:
            try:
                s.limit = int(m.group(1))
                break
            except ValueError:
                continue

    # 排序
    if re.search(r"最多|最高|排名|TOP|前\s*\d", q, re.I):
        s.order = {"by": s.metrics[0] if s.metrics else "value", "dir": "desc"}
    elif re.search(r"最少|最低|倒数", q):
        s.order = {"by": s.metrics[0] if s.metrics else "value", "dir": "asc"}

    # 去重
    s.metrics = list(dict.fromkeys(s.metrics))
    s.dimensions = list(dict.fromkeys(s.dimensions))

    # 槽位是多轮对话与 SQL 生成的唯一输入，抽错一个字段就会表现为「数字对不上」。
    # 这里必须把完整结果留在日志里——排查时先看 limit 是否被时间跨度污染。
    log_kv(
        logger, logging.DEBUG, "槽位抽取完成",
        question=q, metrics=s.metrics, dimensions=s.dimensions,
        time_range=s.time_range, limit=s.limit, compare=s.compare,
        subject=s.subject, filters=s.filters, order=s.order,
    )
    return s


def is_continuation(question: str) -> bool:
    q = question or ""
    if any(mk in q for mk in CONTINUATION_MARKERS):
        return True
    # 极短的追问（≤6 字）也视为续问，如 "同比呢"、"分布呢"
    return len(q.strip()) <= 6


def _covers_subject(f: dict, subject: Optional[str]) -> bool:
    """该筛选条件只是在重复「主体」（已在句中单独输出，不必再说一遍）。"""
    if not subject:
        return False
    value = f.get("value")
    if isinstance(value, list):
        return subject in value
    return value == subject


def _filter_text(f: dict, dim_names: dict) -> str:
    """把一条筛选条件渲染成中文片段（含 != 与多值）。"""
    dim = dim_names.get(f.get("dim"), f.get("dim"))
    op = str(f.get("op") or "=")
    return f"{dim}{OP_LABELS.get(op, op)}{filter_value_text(f.get('value'))}"


def build_question(slots: Slots, schema: SchemaContext) -> str:
    """把槽位还原成一句完整的中文问题。"""
    metric_names = {m["code"]: m["name"] for m in schema.metrics}
    dim_names = {d["code"]: d["name"] for d in schema.dimensions}

    parts: list[str] = []
    t = slots.time_range or {}
    if t.get("type") == "year":
        parts.append(f"{t.get('value')}年")
    elif t.get("type") == "quarter":
        parts.append(str(t.get("value")))
    elif t.get("type") == "last_months":
        parts.append(f"近{t.get('value')}个月")

    if slots.subject:
        parts.append(slots.subject)

    # 主体已单独输出，这里只补「非主体」的筛选条件；
    # 多值（op="in"）条件整体保留，不要只取第一个取值。
    extra_filters = [f for f in slots.filters if not _covers_subject(f, slots.subject)]
    if extra_filters:
        parts.append("、".join(_filter_text(f, dim_names) for f in extra_filters))

    if slots.metrics:
        parts.append("、".join(metric_names.get(m, m) for m in slots.metrics))
    else:
        parts.append("数据")

    if slots.dimensions:
        parts.append("按" + "、".join(dim_names.get(d, d) for d in slots.dimensions) + "统计")

    q = "".join(parts)

    if slots.compare == "yoy":
        q += "，并给出同比"
    if slots.order:
        q += "，按指标降序排名" if slots.order.get("dir") == "desc" else "，按指标升序排名"
    if slots.limit:
        q += f"，取前{slots.limit}"
    return q


async def rewrite(
    question: str,
    prev_slots: Slots,
    schema: SchemaContext,
    *,
    default_year: int = 2026,
    providers: Optional[list] = None,
    history: str = "",
) -> RewriteResult:
    """执行改写。

    槽位抽取分两步（技术方案 §4.2「规则优先，LLM 兜底」）：
      1. 规则层字面匹配 —— 同步、零延迟、确定性、可单测；
      2. 规则层自评拿不准（置信度不足 / 存在否定·歧义·多值·区间信号）时，
         用轻量模型补一次结构化抽取，输出过语义层白名单后才采纳。

    第 2 步失败一律静默回落第 1 步结果，因此无可用模型时行为与纯规则一致。
    """
    cur = extract_slots(question, schema, default_year=default_year)

    # ── LLM 兜底：只在规则层拿不准时才发起，绝大多数问法不会走到这里 ──
    used_llm = False
    assessment = slot_llm.assess(question, cur, schema)
    if assessment.needs_llm():
        llm_slots = await slot_llm.extract_slots_llm(
            question, schema, providers, default_year
        )
        if llm_slots is not None:
            cur = slot_llm.combine(cur, llm_slots, assessment.suspicious)
            used_llm = True
        log_kv(
            logger, logging.DEBUG, "槽位兜底抽取",
            question=question,
            confidence=assessment.confidence,
            issues=assessment.issues,
            suspicious=sorted(assessment.suspicious),
            llm_used=used_llm,
            final_slots=cur.to_dict(),
        )

    merged = merge(prev_slots, cur)

    # 追问为什么「没继承到时间范围」，九成是看这里：merged 是否真的带上了 prev 的槽位
    log_kv(
        logger, logging.DEBUG, "槽位合并完成",
        question=question,
        prev_slots=prev_slots.to_dict(),
        cur_slots=cur.to_dict(),
        merged_slots=merged.to_dict(),
        used_llm=used_llm,
    )

    # 完全没有分析对象，且也没有历史可继承 → 请求澄清（给出指标 + 示例问题）
    if merged.is_empty():
        options = [m["name"] for m in schema.metrics[:4]]
        options += [d["name"] for d in schema.dimensions[:2]]
        options.append("2026年各经营单元收入排名")
        log_kv(logger, logging.DEBUG, "槽位为空，转为澄清", question=question, reason="未能识别要分析的指标或对象")
        return RewriteResult(
            rewritten=question,
            current=cur,
            merged=merged,
            need_clarify=True,
            options=list(dict.fromkeys(options))[:8],
            reason="未能识别要分析的指标或对象",
            used_llm=used_llm,
        )

    # ① 续问优先：即便抽取到了主体（如「那北京呢」），也需要补全继承条件后才可执行
    if is_continuation(question):
        expanded = build_question(merged, schema)
        logger.info("指代消解：「%s」→「%s」", question, expanded)
        return RewriteResult(
            rewritten=expanded, current=cur, merged=merged, used_llm=used_llm
        )

    # ② 自足问题（本轮含指标或维度）→ 原样使用，仅把继承条件附在后面作为约束
    if cur.metrics or cur.dimensions:
        hint = _inherited_hint(prev_slots, cur, schema)
        rewritten = f"{question}（继承：{hint}）" if hint else question
        log_kv(logger, logging.DEBUG, "改写分支②自足问题", branch=2, rewritten=rewritten, inherited=hint)
        return RewriteResult(
            rewritten=rewritten, current=cur, merged=merged, used_llm=used_llm
        )

    # ③ 条件叠加：本轮只有筛选/修饰，把继承条件补全后附加到原问题
    hint = _inherited_hint(prev_slots, cur, schema)
    rewritten = f"{question}（{hint}）" if hint else question
    log_kv(logger, logging.DEBUG, "改写分支③条件叠加", branch=3, rewritten=rewritten, inherited=hint)
    return RewriteResult(
        rewritten=rewritten, current=cur, merged=merged, used_llm=used_llm
    )


def _inherited_hint(prev: Slots, cur: Slots, schema: SchemaContext) -> str:
    """生成本轮从上文继承下来的条件说明。"""
    metric_names = {m["code"]: m["name"] for m in schema.metrics}
    parts: list[str] = []

    t = prev.time_range or {}
    if t and not cur.time_range:
        parts.append(f"{t.get('value')}年" if t.get("type") == "year" else str(t.get("value")))
    if prev.subject and not cur.subject:
        parts.append(prev.subject)
    dim_names = {d["code"]: d["name"] for d in schema.dimensions}
    for f in prev.filters:
        if not any(c.get("dim") == f.get("dim") for c in cur.filters):
            parts.append(_filter_text(f, dim_names))
    if prev.metrics and not cur.metrics:
        parts.append("、".join(metric_names.get(m, m) for m in prev.metrics))
    return "，".join(parts)
