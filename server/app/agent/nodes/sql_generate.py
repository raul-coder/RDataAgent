"""SQL 生成节点：Prompt 组装 + 模型调用 + 结果解析。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.exceptions import AppException, ErrorCode
from ...core.logging import get_logger, log_kv
from ...llm.provider import LLMMessage, extract_json
from ...llm.router import complete
from ...models import SemFewshot
from .retrieve import SchemaContext, source_names

logger = get_logger(__name__)


@dataclass
class SQLDraft:
    sql: str
    thought: str = ""
    source_ids: Optional[list[int]] = None
    chart: Optional[dict] = None
    confidence: float = 0.0
    model: str = ""
    tokens: dict = field(default_factory=dict)


SYSTEM_PROMPT = """你是经管之星平台的经营数据分析引擎，负责把用户的中文问题翻译成 PostgreSQL SQL。

# 硬性约束
1. 只能使用下方「可用指标」「可用维度」中列出的表达式，禁止臆造任何列名或表名。
2. 只输出 SELECT 语句（允许 CTE 与子查询），禁止 INSERT/UPDATE/DELETE/DDL 与多语句。
3. 必须使用给定的表别名；JOIN 维表时别名不可省略。
4. 必须包含时间过滤；用户未指定时间时默认 year = {default_year}。
5. 分组字段必须出现在 SELECT 中；需要中文展示时 JOIN 维表取名称列。
6. 未指定条数时：排名类 LIMIT 10，其他 LIMIT 1000；结果行数上限 {max_rows}。
7. 只输出问题真正需要的列，不要额外添加 unit_code 等辅助列；
   服务端会自动按表别名施加数据权限，无需在 SELECT 中体现。

# 输出格式（严格 JSON，不要输出多余文字）
{{"thought":"一句话说明选表与聚合逻辑","sql":"SELECT ...","chart":{{"type":"bar|line|pie|table|metric","x":"...","y":"..."}},"confidence":0.0~1.0}}
"""


async def generate_sql(
    db: AsyncSession,
    providers: list,
    question: str,
    schema: SchemaContext,
    *,
    history: str = "",
    prev_sql: str = "",
    retry_hint: str = "",
    slot_hint: str = "",
    intent: str = "data_query",
    fewshot_k: int = 3,
) -> SQLDraft:
    """调用模型生成 SQL，返回草稿。

    :param slot_hint: 多轮继承下来的分析条件（会作为强制约束注入 Prompt）
    :param intent: 意图，用于挑选更贴题的 Few-shot
    """
    fewshots = await _pick_fewshots(db, question, k=fewshot_k)

    user_parts = [f"# 当前问题\n{question}"]
    if slot_hint:
        user_parts.append("")
        user_parts.append(slot_hint)
    user_parts.append("")
    user_parts.append(schema.render())
    if fewshots:
        user_parts.append("")
        user_parts.append("【参考示例】")
        for fs in fewshots:
            user_parts.append(f"问：{fs['question']}\nSQL：{fs['sql_text']}")
    if history:
        user_parts.append("")
        user_parts.append(f"【最近对话】\n{history}")
    if prev_sql:
        user_parts.append("")
        user_parts.append(f"【上一轮 SQL（供多轮追问参考）】\n{prev_sql}")
    if retry_hint:
        user_parts.append("")
        user_parts.append(f"【上一次执行失败，请修正】\n{retry_hint}")

    messages = [
        LLMMessage(
            role="system",
            content=SYSTEM_PROMPT.format(
                default_year=settings.DEFAULT_YEAR, max_rows=settings.SQL_MAX_ROWS
            ),
        ),
        LLMMessage(role="user", content="\n".join(user_parts)),
    ]

    resp = await complete(
        providers,
        messages,
        question=question,
        temperature=0.0,
        json_mode=True,
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    payload = extract_json(resp.content)
    if not payload or not payload.get("sql"):
        log_kv(logger, logging.WARNING, "模型未返回可解析的 SQL", question=question,
               raw=resp.content[:400], model=resp.model)
        raise AppException("模型未返回可解析的 SQL", ErrorCode.LLM_ERROR)

    # 完整 SQL 必须留痕：这是后续所有「结果不对」类问题的起点。
    # 注意此时还没过校验与权限注入，落地执行的版本见 sql_validate 的日志。
    log_kv(
        logger, logging.DEBUG, "SQL 生成完成",
        question=question, sql=payload.get("sql"),
        thought=payload.get("thought"), confidence=payload.get("confidence"),
        model=resp.model,
        prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
        fewshot_ids=[fs["id"] for fs in fewshots],
        has_slot_hint=bool(slot_hint), has_history=bool(history),
        has_prev_sql=bool(prev_sql), is_retry=bool(retry_hint),
    )

    return SQLDraft(
        sql=str(payload["sql"]).strip().rstrip(";"),
        thought=str(payload.get("thought", "") or ""),
        source_ids=[int(i) for i in (payload.get("data_source_ids") or [])] or None,
        chart=payload.get("chart") if isinstance(payload.get("chart"), dict) else None,
        confidence=float(payload.get("confidence") or 0.0),
        model=resp.model,
        tokens={
            "prompt": resp.prompt_tokens,
            "completion": resp.completion_tokens,
        },
    )


async def _pick_fewshots(db: AsyncSession, question: str, k: int = 3) -> list[dict]:
    """按 2-gram 相似度挑选最相近的 Few-shot 样本。"""
    from ...llm.template_provider import similarity

    rows = (
        await db.execute(
            select(
                SemFewshot.id,
                SemFewshot.question,
                SemFewshot.sql_text,
                SemFewshot.verified,
            )
        )
    ).all()
    scored = []
    for r in rows:
        score = similarity(question, r[1] or "")
        if r[3]:
            score *= 1.05
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [
        {"id": r[0], "question": r[1], "sql_text": r[2]}
        for score, r in scored[:k]
        if score > 0.05
    ]
