"""意图识别：9 类意图 + 结果二次加工 + 越界拒答。

意图决定处理方式：
    result_ops   —— 排序/改图/导出，只改呈现，不重跑 SQL
    out_of_scope / chitchat —— 直接模板回复，不查库
    其余         —— 走完整取数链路
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from ...core.logging import get_logger, log_kv

logger = get_logger(__name__)

# 结果二次加工
OPS_PATTERNS = {
    "sort": re.compile(r"排序|升序|降序|倒序|从大到小|从小到大|反过来"),
    "chart": re.compile(r"换成|改成|改一下|用饼图|用饼状|用柱状|用条形|用折线|用曲线|画个|画一张|图表"),
    "export": re.compile(r"导出|下载|存成|生成\s*(excel|csv)"),
    "topn": re.compile(r"只看前|显示前|取前|前\s*\d+\s*个"),
}

# 越界（与经营数据无关）
OUT_OF_SCOPE_PATTERNS = re.compile(
    r"天气|股票|基金|彩票|电影|游戏|做饭|菜谱|翻译|写(一首|篇|封|个)(诗|文章|信|故事)|"
    r"讲个笑话|你是谁|你叫什么|人格|感情|算命|"
    # 代码 / 程序 / 脚本类：要求动词在前面且距离受限，避免误伤「北京代表处的代码」这类业务表述
    r"(写|编|生成|来|搞)[^。？]{0,10}(代码|程序|脚本)|编程|"
    r"帮我(写|编|生成|做)(文案|周报|总结|邮件|PPT)"
)

CHITCHAT_PATTERNS = re.compile(r"^(你好|您好|hi|hello|谢谢|感谢|再见|拜拜|ok|好的)[\s!！。.]*$", re.I)

INTENTS = (
    "data_query",     # 常规取数
    "compare",        # 对比（目标 vs 实际、A vs B）
    "ranking",        # 排名 TOP N
    "trend",          # 趋势（月度/季度）
    "proportion",     # 占比
    "attribution",    # 归因（为什么下降/未完成）
    "warning",        # 预警/风险
    "result_ops",     # 结果二次加工
    "chitchat",       # 闲聊
    "out_of_scope",   # 越界
)


@dataclass
class IntentResult:
    intent: str
    op: Optional[str] = None      # result_ops 的具体操作：sort / chart / export / topn
    confidence: float = 1.0
    reply: str = ""               # chitchat / out_of_scope 的直接回复


def classify(question: str) -> IntentResult:
    """规则优先的意图识别（低延迟、可预测）。

    外面包一层只为统一打点：判定逻辑本身有多个 return，
    逐个加日志会淹没重点，收在单一出口更清晰。
    """
    result = _classify_inner(question)
    # 走错分支（该查库却当成二次加工、或反之）时，第一条要看的就是这里
    log_kv(
        logger, logging.DEBUG, "意图识别完成",
        question=question, intent=result.intent, op=result.op,
        confidence=result.confidence,
    )
    return result


def _classify_inner(question: str) -> IntentResult:
    q = (question or "").strip()

    if not q:
        return IntentResult("data_query", confidence=0.0)

    if CHITCHAT_PATTERNS.match(q):
        return IntentResult(
            "chitchat", confidence=0.95,
            reply="你好！我是经管之星的问数助手，可以帮你查询经营数据，"
                  "比如「2026年各经营单元收入排名」「北京代表处今年达成情况」。",
        )

    if OUT_OF_SCOPE_PATTERNS.search(q):
        return IntentResult(
            "out_of_scope", confidence=0.9,
            reply="抱歉，我只能回答**经营数据**相关的问题（收入、目标、达成率、回款、"
                  "产品线、行业、风险项目等）。试试问我「2026年各经营单元收入排名」？",
        )

    for op, pat in OPS_PATTERNS.items():
        if pat.search(q):
            return IntentResult("result_ops", op=op, confidence=0.85)

    if re.search(r"同比|同期|增长|增速|对比|相比|vs", q, re.I):
        return IntentResult("compare", confidence=0.8)
    if re.search(r"为什么|原因|归因|怎么(下降|下滑)|为何", q):
        return IntentResult("attribution", confidence=0.8)
    if re.search(r"风险|预警|低达成|未完成|缺口", q):
        return IntentResult("warning", confidence=0.8)
    if re.search(r"占比|比例|构成|份额", q):
        return IntentResult("proportion", confidence=0.85)
    if re.search(r"趋势|每月|月度|季度|走势|变化", q):
        return IntentResult("trend", confidence=0.85)
    if re.search(r"排名|TOP|前\s*\d|最多|最高|最少|最低", q, re.I):
        return IntentResult("ranking", confidence=0.85)

    return IntentResult("data_query", confidence=0.6)


def needs_full_pipeline(result: IntentResult) -> bool:
    """是否需要走完整取数链路。"""
    return result.intent not in {"result_ops", "chitchat", "out_of_scope"}
