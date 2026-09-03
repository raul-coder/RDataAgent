"""结论生成节点：把取数结果翻译成人话。

关键设计：**数值由程序算好再注入 Prompt**（见技术方案 §4.3⑨），
让模型只做「表述」不做「计算」，杜绝心算错误。
模型不可用时回退到程序化模板，保证链路不中断。
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterator

from ...core.config import settings
from ...core.logging import get_logger
from ...llm.provider import LLMMessage
from ...llm.router import stream_complete

logger = get_logger(__name__)

SYSTEM = """你是经管之星的问数助手，负责把数据查询结果写成简洁的中文经营分析结论。

规则：
1. 只使用「统计摘要」中给出的数值，禁止自行计算或编造数字。
2. 用 Markdown，3~5 句话，先给结论再给关键数据。
3. 必须点出：最大值/最小值（或排名第一）、合计、与目标/同期的对比（若数据里有）。
4. 若结果为空，明确说明「在该条件下无数据」并给出可放宽的建议。
5. 不要复述 SQL，不要出现"根据查询结果"这类空话。
6. 金额类指标的**单位是「万元」**（平台统一口径），不要写成"元"。
7. 关于数据权限（**极易出错，务必严格遵守**）：
   · 上下文存在「# 数据权限说明」这一节时，必须在结论末尾补一句提示，
     单元名称**照抄该节原文**。
   · 上下文**没有**这一节时，**严禁**提及任何数据权限、可见范围、过滤、
     经营单元限制等内容——此时结果基于全量数据，声称被过滤会直接误导用户。
   · 不要凭印象套用任何单元名称。
8. 结果为空且存在「数据权限说明」时，要说明这是**权限范围所致**，
   不要让用户误以为数据不存在或名称写错（禁止出现"名称是否准确"这类猜测）。
"""

#: 权限提示的固定开头。用于「未提供权限说明却出现该提示」时的兜底清理。
_PERM_NOTE_PREFIX = "注：以上结果已按您的数据权限范围过滤"

_PERM_LINE_RE = re.compile(
    r"[^\n。]*" + re.escape(_PERM_NOTE_PREFIX) + r"[^\n。]*。[ \t]*(?:\n|$)"
)


def strip_false_permission_note(text: str, permission_note: str) -> str:
    """未提供数据权限说明时，剔除结论里凭空出现的权限提示。

    为什么需要兜底：模型偶发把规则里的示例原样吐出来（历史数据 30 条带备注的
    回答里，6 条 SQL 中根本没有 unit_code 过滤——都是管理员账号）。这属于
    **数据可信度**问题：声称被过滤、实际是全量，比不说更糟，因此提示词修正
    之外再加一道程序化清理。

    :param permission_note: 真实提供的权限说明；非空表示提示合法，原样返回
    """
    if not text or permission_note:
        return text
    cleaned = _PERM_LINE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

FALLBACK_TEMPLATE = """查询返回 **{rows} 行**数据{extra}。

{body}
"""


def _fmt_num(v: Any) -> str:
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def build_context(stats: dict, columns: list[str], rows: list[list[Any]], limit: int = 12) -> str:
    """构造喂给模型的结果上下文（摘要 + 预览）。"""
    lines = [f"# 结果列\n{', '.join(columns)}", "", f"# 统计摘要（共 {stats.get('row_count', 0)} 行）"]

    for col, s in stats.items():
        if not isinstance(s, dict) or "sum" not in s:
            continue
        if {"max", "min", "avg"} <= set(s.keys()):
            lines.append(
                f"- {col}：合计 {_fmt_num(s['sum'])}，最大 {_fmt_num(s['max'])}，"
                f"最小 {_fmt_num(s['min'])}，均值 {_fmt_num(s['avg'])}"
            )
        else:
            lines.append(f"- {col}：{_fmt_num(s['sum'])}")

    preview = rows[:limit]
    if preview:
        lines.append("")
        lines.append("# 结果预览（前 %d 行）" % len(preview))
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
        for r in preview:
            lines.append("| " + " | ".join(_fmt_num(v) for v in r) + " |")

    if stats.get("truncated"):
        lines.append("")
        lines.append("（结果已截断，仅展示部分）")
    return "\n".join(lines)


async def stream_answer(
    providers: list,
    question: str,
    context: str,
    permission_note: str = "",
) -> AsyncIterator[str]:
    """流式生成结论文本。

    只使用「真实模型」Provider：检索式兜底 Provider 只能产出 SQL，
    拿它写结论会得到无意义文本，因此这里直接抛错交给程序化摘要兜底。

    permission_note：数据权限说明（为空表示用户无行级限制）。
    传入时模型必须在结论中提示过滤范围，避免用户把"无权查看"误读成"没有数据"。
    """
    remote = [p for p in providers if getattr(p, "is_remote", False)]
    if not remote:
        raise RuntimeError("无可用真实模型，结论生成走程序化摘要")

    user_content = f"# 用户问题\n{question}\n\n{context}"
    if permission_note:
        user_content += f"\n\n# 数据权限说明\n{permission_note}"
    user_content += "\n\n请给出结论。"

    messages = [
        LLMMessage(role="system", content=SYSTEM),
        LLMMessage(role="user", content=user_content),
    ]
    got = False
    async for delta in stream_complete(
        remote, messages, question=question, temperature=0.3, max_tokens=settings.LLM_MAX_TOKENS
    ):
        if delta:
            got = True
            yield delta

    if not got:
        # 推理型模型可能把预算全用在思维链上，导致正文为空 —— 交给程序化摘要兜底
        raise RuntimeError("模型未产出正文内容（可能被思维链耗尽预算）")


def fallback_answer(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    stats: dict,
    permission_note: str = "",
) -> str:
    """模型不可用时的程序化结论（保证链路不中断）。"""
    n = stats.get("row_count", 0)
    note = f"\n\n注：本次查询{permission_note}。" if permission_note else ""

    if n == 0:
        if permission_note:
            # 有权限限制时，空结果几乎一定是权限所致，
            # 不能再让用户去"检查名称是否准确"
            return (
                f"在「{question}」的条件下**没有查询到数据**。\n\n"
                f"本次查询{permission_note}，因此未返回任何记录。\n"
                "如需查看其他经营单元的数据，请联系管理员开通数据权限。"
            )
        return (
            f"在「{question}」的条件下**没有查询到数据**。\n\n"
            "建议放宽条件后重试，例如：\n"
            "- 扩大时间范围（如改为 2025–2026 年）\n"
            "- 去掉部分筛选条件\n"
            "- 确认经营单元 / 行业名称是否准确"
        )

    body_parts: list[str] = []

    # 主指标极值
    for col, s in stats.items():
        if not isinstance(s, dict) or "sum" not in s:
            continue
        if {"max", "min", "avg"} <= set(s.keys()):
            body_parts.append(
                f"- **{col}**：合计 {_fmt_num(s['sum'])}，"
                f"最高 {_fmt_num(s['max'])}，最低 {_fmt_num(s['min'])}，均值 {_fmt_num(s['avg'])}"
            )
        else:
            body_parts.append(f"- **{col}**：{_fmt_num(s['sum'])}")

    # 排名第一
    if rows and len(columns) >= 2:
        first_col = 0
        num_j = next(
            (j for j in range(len(columns))
             if any(isinstance(r[j], (int, float)) and not isinstance(r[j], bool) for r in rows)),
            None,
        )
        if num_j is not None and num_j != first_col:
            top = max(
                (r for r in rows if isinstance(r[num_j], (int, float))),
                key=lambda r: r[num_j],
                default=None,
            )
            if top is not None:
                body_parts.append(
                    f"- 排名第一：**{top[first_col]}**（{columns[num_j]} {_fmt_num(top[num_j])}）"
                )

    extra = f"，展示前 {min(n, 12)} 行" if n > 12 else ""
    return FALLBACK_TEMPLATE.format(
        rows=n, extra=extra, body="\n".join(body_parts) or "- 已返回结果数据，详见下表。"
    ) + note


def suggest_followups(question: str, columns: list[str]) -> list[str]:
    """生成追问建议（I3 会改为模型生成；此处为确定性模板）。"""
    out: list[str] = []

    if not any(k in question for k in ("同比", "同期", "去年")):
        out.append("同比情况如何？")
    if not any(k in question for k in ("趋势", "每月", "月度")):
        out.append("按月的趋势是怎样的？")
    if not any(k in question for k in ("产品线", "型号")):
        out.append("拆到产品线看看")
    if not any(k in question for k in ("目标", "达成", "完成率")):
        out.append("对应目标的达成情况？")
    if not any(k in question for k in ("行业",)):
        out.append("按行业维度拆分")

    # 过滤掉与当前问题高度重复的
    result = [q for q in out if q]
    return result[:3]
