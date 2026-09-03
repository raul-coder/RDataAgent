"""槽位（Slots）：多轮对话的状态载体。

为什么用显式槽位而不是「把历史消息拼进 Prompt」：
    指代（"那北京呢"）、条件叠加（"只看政企"）、时间切换（"同比呢"）
    都需要**结构化地继承与覆盖**上一次的分析条件。
    槽位让这个过程可预测、可调试、可测试。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

#: 筛选算子 → 中文展示。Prompt 里给模型看中文比看 "="、"!=" 更不容易误解。
OP_LABELS: dict[str, str] = {"=": "为", "!=": "不为", "in": "属于", "not_in": "不属于"}


def filter_value_text(value: Any) -> str:
    """筛选取值的展示文本（in 算子是数组，需展开）。"""
    if isinstance(value, list):
        return "、".join(str(v) for v in value)
    return str(value)


@dataclass
class Slots:
    metrics: list[str] = field(default_factory=list)          # ["biz_income"]
    dimensions: list[str] = field(default_factory=list)       # ["unit"]
    # op 取值：=（等于）/ !=（不等于）/ in（多值，value 为数组）
    filters: list[dict] = field(default_factory=list)         # [{"dim":"industry_cat","op":"=","value":"政企"}]
    time_range: dict = field(default_factory=dict)            # {"type":"year","value":2026}
    compare: Optional[str] = None                             # yoy / mom / qoq / None
    order: Optional[dict] = None                              # {"by":"biz_income","dir":"desc"}
    limit: Optional[int] = None
    subject: Optional[str] = None                             # 当前分析主体，如 "北京代表处"
    chart_hint: Optional[str] = None

    def is_empty(self) -> bool:
        return not (
            self.metrics or self.dimensions or self.filters
            or self.time_range or self.compare or self.order
            or self.limit or self.subject
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Slots":
        if not d:
            return cls()
        return cls(
            metrics=list(d.get("metrics") or []),
            dimensions=list(d.get("dimensions") or []),
            filters=[dict(f) for f in (d.get("filters") or [])],
            time_range=dict(d.get("time_range") or {}),
            compare=d.get("compare"),
            order=dict(d["order"]) if d.get("order") else None,
            limit=d.get("limit"),
            subject=d.get("subject"),
            chart_hint=d.get("chart_hint"),
        )


def flatten_values(value_map) -> list[str]:
    """把维度取值表摊平成取值列表。

    取值表可能是 dict（按区域分组，如 {"华北":["北京代表处",...]}）
    或 list（如 ["政企","运营商"]），这里统一摊平，供取值匹配与白名单校验使用。
    """
    values: list[str] = []
    if isinstance(value_map, dict):
        for v in value_map.values():
            if isinstance(v, list):
                values.extend(str(x) for x in v)
            else:
                values.append(str(v))
    elif isinstance(value_map, list):
        values.extend(str(x) for x in value_map)
    return [v for v in values if v]


# 机构后缀：用户常把「北京代表处」说成「北京」
ORG_SUFFIXES = re.compile(r"(代表处|办事处|系统部|分公司|子公司|有限公司|公司|中心|事业部|部门)$")

#: 否定词词表。规则层据此判定筛选算子（= / !=），兜底层据此判定「可疑」，
#: 两边共用一份，避免各存一套导致判定漂移。
NEGATION_MARKERS: tuple[str, ...] = (
    "不含", "不包含", "除了", "排除", "剔除", "不算", "不要", "不是",
)
#: 单字否定词成词性弱，需排除「非常 / 非凡 / 非法 / 非洲」这类误伤
NEGATION_WEAK_RE = re.compile(r"非(?!常|凡|法|洲)")


def has_negation(text: str) -> bool:
    """文本中是否出现否定词。"""
    t = text or ""
    return any(m in t for m in NEGATION_MARKERS) or bool(NEGATION_WEAK_RE.search(t))


def alias_keys(value: str) -> list[str]:
    """为一个取值生成可用于匹配的别名（含去掉机构后缀的简称）。"""
    keys = [value]
    short = ORG_SUFFIXES.sub("", value)
    if len(short) >= 2 and short != value:
        keys.append(short)
    return keys


def match_hits(question: str, values: list[str]) -> list[tuple[str, str]]:
    """在问题中匹配取值，支持简称。

    返回 [(命中的键, 完整取值)]，按命中键长度降序——
    保证「北京」优先匹配到「北京代表处」而非其它更短的取值。
    返回「键」是为了让调用方能定位取值在问句中的**位置**（否定判定要用）。
    """
    hits: list[tuple[int, str, str]] = []
    for v in values:
        for key in alias_keys(v):
            if key and key in question:
                hits.append((len(key), key, v))
                break

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for _, key, v in sorted(hits, key=lambda x: -x[0]):
        if v not in seen:
            seen.add(v)
            out.append((key, v))
    return out


def match_values(question: str, values: list[str]) -> list[str]:
    """在问题中匹配取值，返回完整取值列表（按命中长度降序）。"""
    return [v for _, v in match_hits(question, values)]


def merge(prev: Slots, cur: Slots) -> Slots:
    """合并槽位：本轮显式命中的覆盖历史，未命中的继承历史。

    过滤条件按 (dim, op) 去重：
      · 同维度的新条件替换旧的 → 实现「条件叠加」与「条件切换」；
      · op="in" 的多值条件取**并集** → 否则「北京和上海」会被后一个覆盖，
        只剩一个取值（见 tests/test_multiturn.py）。
    """
    out = Slots()

    out.metrics = cur.metrics or list(prev.metrics)
    out.dimensions = cur.dimensions or list(prev.dimensions)

    # 过滤条件：按 (dim, op) 覆盖；in 条件合并取值
    merged: dict[tuple[str, str], dict] = {}
    for f in [*prev.filters, *cur.filters]:
        dim = str(f.get("dim"))
        op = str(f.get("op") or "=")
        value = f.get("value")
        key = (dim, op)
        if op == "in":
            base = merged.get(key, {}).get("value")
            prev_vals = base if isinstance(base, list) else ([base] if base is not None else [])
            add_vals = value if isinstance(value, list) else ([value] if value is not None else [])
            # dict.fromkeys 去重且保持顺序
            merged[key] = {
                "dim": dim,
                "op": "in",
                "value": list(dict.fromkeys([*prev_vals, *add_vals])),
            }
        else:
            merged[key] = {"dim": dim, "op": op, "value": value}
    out.filters = list(merged.values())

    out.time_range = dict(cur.time_range) if cur.time_range else dict(prev.time_range)
    out.compare = cur.compare or prev.compare
    out.order = dict(cur.order) if cur.order else (dict(prev.order) if prev.order else None)
    out.limit = cur.limit if cur.limit is not None else prev.limit
    out.subject = cur.subject or prev.subject
    out.chart_hint = cur.chart_hint or prev.chart_hint
    return out


def describe(slots: Slots, *, metric_names: dict | None = None) -> str:
    """把槽位渲染成自然语言，用于澄清卡片与上下文展示。"""
    if slots.is_empty():
        return ""
    parts: list[str] = []
    mn = metric_names or {}

    if slots.subject:
        parts.append(f"主体：{slots.subject}")
    if slots.dimensions:
        parts.append("维度：" + "、".join(slots.dimensions))
    if slots.metrics:
        parts.append("指标：" + "、".join(mn.get(m, m) for m in slots.metrics))
    if slots.time_range:
        t = slots.time_range
        parts.append(f"时间：{t.get('value', '')}{t.get('type', '')}")
    if slots.compare:
        parts.append("对比：" + {"yoy": "同比", "mom": "环比", "qoq": "环比（季度）"}.get(slots.compare, slots.compare))
    for f in slots.filters:
        op = str(f.get("op") or "=")
        label = OP_LABELS.get(op, op)
        parts.append(f"筛选：{f.get('dim')} {label} {filter_value_text(f.get('value'))}")
    if slots.order:
        parts.append(f"排序：{slots.order.get('by')} {slots.order.get('dir')}")
    if slots.limit:
        parts.append(f"条数：{slots.limit}")
    return "，".join(parts)


def to_prompt_hint(slots: Slots, *, metric_names: dict | None = None) -> str:
    """渲染成注入 Prompt 的上下文提示。"""
    text = describe(slots, metric_names=metric_names)
    return f"【继承的上文分析条件】{text}\n请把这些条件一并体现在 SQL 的 WHERE 中。" if text else ""
