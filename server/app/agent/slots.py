"""槽位（Slots）：多轮对话的状态载体。

为什么用显式槽位而不是「把历史消息拼进 Prompt」：
    指代（"那北京呢"）、条件叠加（"只看政企"）、时间切换（"同比呢"）
    都需要**结构化地继承与覆盖**上一次的分析条件。
    槽位让这个过程可预测、可调试、可测试。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Slots:
    metrics: list[str] = field(default_factory=list)          # ["biz_income"]
    dimensions: list[str] = field(default_factory=list)       # ["unit"]
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


def merge(prev: Slots, cur: Slots) -> Slots:
    """合并槽位：本轮显式命中的覆盖历史，未命中的继承历史。

    过滤条件按维度去重（同一维度的新条件替换旧的），实现「条件叠加」。
    """
    out = Slots()

    out.metrics = cur.metrics or list(prev.metrics)
    out.dimensions = cur.dimensions or list(prev.dimensions)

    # 过滤条件：按 dim 维度覆盖
    merged: dict[str, dict] = {}
    for f in prev.filters:
        merged[str(f.get("dim"))] = dict(f)
    for f in cur.filters:
        merged[str(f.get("dim"))] = dict(f)
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
        parts.append(f"筛选：{f.get('dim')} {f.get('op', '=')} {f.get('value')}")
    if slots.order:
        parts.append(f"排序：{slots.order.get('by')} {slots.order.get('dir')}")
    if slots.limit:
        parts.append(f"条数：{slots.limit}")
    return "，".join(parts)


def to_prompt_hint(slots: Slots, *, metric_names: dict | None = None) -> str:
    """渲染成注入 Prompt 的上下文提示。"""
    text = describe(slots, metric_names=metric_names)
    return f"【继承的上文分析条件】{text}\n请把这些条件一并体现在 SQL 的 WHERE 中。" if text else ""
