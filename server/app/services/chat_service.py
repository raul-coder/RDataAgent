"""会话服务：会话/消息 CRUD + 问数流式编排。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import events as ev
from ..agent.runtime import AI_QA_MENU_ID, AgentRuntime
from ..core.config import settings
from ..core.deps import CurrentUser
from ..core.exceptions import NotFoundError
from ..core.logging import get_logger, log_kv
from ..db.session import readonly_session
from ..models import ChatMessage, ChatSession, QaFeedback
from . import qa_cache

logger = get_logger(__name__)

MAX_HISTORY_TURNS = 6  # I3 将改为「超出部分自动摘要」


def _visible_units(user: CurrentUser) -> Optional[list[str]]:
    """问数菜单的数据权限范围（与 AgentRuntime._visible_units 保持一致）。

    抽出来是因为缓存键必须带上它：不同权限的用户不能共用同一份结果。
    """
    if user.is_superadmin:
        return None
    raw = (user.data_perms or {}).get(str(AI_QA_MENU_ID))
    if raw is None:
        return None
    return list(raw) if raw else None


# ── 会话 ────────────────────────────────────────────────────────────
async def list_sessions(
    db: AsyncSession,
    user_id: int,
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    stmt = select(ChatSession).where(
        ChatSession.user_id == user_id, ChatSession.deleted_at.is_(None)
    )
    if keyword:
        stmt = stmt.where(ChatSession.title.ilike(f"%{keyword}%"))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(ChatSession.pinned.desc(), ChatSession.last_msg_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "items": [_session_vo(s) for s in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _session_vo(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "pinned": bool(s.pinned),
        "msg_count": s.msg_count,
        "user_feedback": s.user_feedback or "",
        "admin_feedback": s.admin_feedback or "",
        "source_files": list(s.source_files or []),
        "last_msg_at": s.last_msg_at.strftime("%Y-%m-%d %H:%M:%S") if s.last_msg_at else "",
        "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
    }


async def create_session(db: AsyncSession, user_id: int, title: str = "新对话") -> ChatSession:
    s = ChatSession(
        user_id=user_id,
        title=title,
        pinned=False,
        msg_count=0,
        source_files=[],
        last_msg_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db.add(s)
    await db.flush()
    return s


async def get_session(db: AsyncSession, user_id: int, session_id: int) -> Optional[ChatSession]:
    res = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
            ChatSession.deleted_at.is_(None),
        )
    )
    return res.scalar_one_or_none()


async def rename_session(db: AsyncSession, user_id: int, session_id: int, title: str) -> None:
    s = await get_session(db, user_id, session_id)
    if not s:
        raise NotFoundError("会话不存在")
    s.title = title
    await db.flush()


async def pin_session(db: AsyncSession, user_id: int, session_id: int, pinned: bool) -> None:
    s = await get_session(db, user_id, session_id)
    if not s:
        raise NotFoundError("会话不存在")
    s.pinned = pinned
    await db.flush()


async def delete_session(db: AsyncSession, user_id: int, session_id: int) -> None:
    s = await get_session(db, user_id, session_id)
    if not s:
        raise NotFoundError("会话不存在")
    s.deleted_at = datetime.now(timezone.utc)
    await db.flush()


async def list_messages(
    db: AsyncSession, user_id: int, session_id: int, page: int = 1, page_size: int = 50
) -> dict:
    s = await get_session(db, user_id, session_id)
    if not s:
        raise NotFoundError("会话不存在")

    stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(ChatMessage.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return {
        "items": [_message_vo(m) for m in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _message_vo(m: ChatMessage) -> dict:
    return {
        "id": m.id,
        "session_id": m.session_id,
        "role": m.role,
        "content": m.content,
        "payload": m.payload,
        "rewritten_query": m.rewritten_query or "",
        "intent": m.intent or "",
        "model": m.model or "",
        "prompt_tokens": m.prompt_tokens or 0,
        "completion_tokens": m.completion_tokens or 0,
        "cost_ms": m.cost_ms or 0,
        "trace_id": m.trace_id or "",
        "error": m.error or "",
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else "",
    }


def _build_history(messages: list[ChatMessage]) -> str:
    turns = messages[-MAX_HISTORY_TURNS * 2 :]
    lines = []
    for m in turns:
        prefix = "用户" if m.role == "user" else "助手"
        text = (m.content or "").strip().replace("\n", " ")
        lines.append(f"{prefix}：{text[:200]}")
    return "\n".join(lines)


def _last_sql(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "assistant" and m.payload and m.payload.get("sql"):
            return str(m.payload["sql"])
    return ""


async def _replay_cached(
    db: AsyncSession,
    session: ChatSession,
    hit: dict,
    question: str,
    user_msg_id: int,
    trace_id: str,
) -> AsyncIterator[ev.SSEEvent]:
    """命中缓存时回放结果。

    仍然要落助手消息并推送 meta（含 AI 消息 id）——否则前端拿不到 id，
    点赞点踩与「数据有误」会因 m.id <= 0 被拦掉。
    """
    content = hit.get("content", "")
    tables = hit.get("tables") or []
    first = tables[0] if tables else {}

    yield ev.meta_event(
        question=question,
        model=hit.get("model", ""),
        degraded=False,
        cached=True,
        data_as_of=settings.DATA_AS_OF,
        session_id=session.id,
    )
    for s in hit.get("steps") or []:
        yield ev.step_event(
            int(s.get("index", 1)), ev.DONE, s.get("desc", ""), int(s.get("cost_ms", 0))
        )
    yield ev.sql_event(hit.get("sql", ""), hit.get("data_sources") or [])
    for t in tables:
        yield ev.table_event(
            t.get("columns") or [], t.get("rows") or [],
            len(t.get("rows") or []), bool(t.get("truncated")),
        )
    for c in hit.get("charts") or []:
        yield ev.chart_event(c.get("option") or {}, c.get("type") or "bar")
    for i in range(0, len(content), 24):
        yield ev.token_event(content[i : i + 24])
    yield ev.followups_event(hit.get("followups") or [])

    ai_msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=content,
        payload={
            "steps": hit.get("steps") or [],
            "sql": hit.get("sql", ""),
            "data_sources": hit.get("data_sources") or [],
            "tables": tables,
            "charts": hit.get("charts") or [],
            "cached": True,
            "model_fallback": bool(hit.get("model_fallback")),
        },
        rewritten_query=hit.get("rewritten", ""),
        intent=hit.get("intent", "data_query"),
        model=hit.get("model", ""),
        trace_id=trace_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(ai_msg)
    session.msg_count = (session.msg_count or 0) + 1
    session.last_msg_at = datetime.now(timezone.utc)
    await db.flush()
    await db.commit()

    # 命中缓存跳过了 Agent，但会话上下文必须照常维护。
    # 否则下一轮追问读到的 prev_slots 为空，会丢掉本轮的筛选条件——
    # 例如 Q1「高风险项目有哪些」命中缓存后，Q2「按产品线分拆看看」
    # 就丢失了 risk_level = '高'，变成统计全部项目。
    await _sync_context_after_cache(session.id, question, hit, first)

    yield ev.meta_event(
        message_id=ai_msg.id, session_id=session.id, trace_id=trace_id, role="assistant"
    )
    yield ev.done_event(
        tokens=hit.get("tokens") or {}, model=hit.get("model", ""),
        cost_ms=0, degraded=False,
        model_fallback=bool(hit.get("model_fallback")),
        rows=len(first.get("rows") or []), cached=True,
    )


async def _sync_context_after_cache(
    session_id: int, question: str, hit: dict, first_table: dict
) -> None:
    """缓存命中的轮次也要更新会话上下文（槽位 + 结果集引用）。

    只补两样东西，且都不需要 LLM：
      - active_slots：由 extract_slots 纯规则抽取，供下一轮追问继承
      - last_result_key / last_sql：供 result_ops（排序 / 换图 / 取前 N）复用

    任何异常都只告警不抛出——上下文同步失败不该影响已经成功的问数。
    """
    try:
        from ..agent import context as ctx_store
        from ..agent.nodes.retrieve import retrieve_schema
        from ..agent.nodes.rewrite import extract_slots
        from ..agent.slots import merge

        async with readonly_session() as ro:
            schema = await retrieve_schema(ro)

        ctx = ctx_store.load(session_id)
        cur = extract_slots(question, schema, default_year=settings.DEFAULT_YEAR)
        ctx.active_slots = merge(ctx.active_slots, cur)
        ctx.last_sql = hit.get("sql", "")
        if first_table:
            ctx.last_result_key = ctx_store.cache_result(
                session_id,
                {
                    "columns": first_table.get("columns") or [],
                    "rows": first_table.get("rows") or [],
                    "total": len(first_table.get("rows") or []),
                    "truncated": bool(first_table.get("truncated")),
                    "chart": (hit.get("charts") or [{}])[0],
                    "question": question,
                    "sql": hit.get("sql", ""),
                },
            )
        ctx.turn_count += 1
        ctx_store.save(ctx)

        log_kv(
            logger, logging.DEBUG, "缓存命中后同步会话上下文",
            session_id=session_id, turn_count=ctx.turn_count,
            active_slots=ctx.active_slots.to_dict(),
            has_last_result=bool(ctx.last_result_key),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("缓存命中后同步会话上下文失败（不影响本次问数）：%s", exc)


# ── 问数主流程 ──────────────────────────────────────────────────────
async def stream_answer(
    db: AsyncSession,
    user: CurrentUser,
    question: str,
    *,
    session_id: Optional[int] = None,
    source_ids: Optional[list[int]] = None,
) -> AsyncIterator[ev.SSEEvent]:
    """流式问数；过程中持久化用户消息与助手消息。"""
    trace_id = uuid.uuid4().hex[:16]

    session = None
    if session_id:
        session = await get_session(db, user.id, session_id)
        if not session:
            yield ev.error_event("SESSION_NOT_FOUND", "会话不存在")
            yield ev.done_event(error="SESSION_NOT_FOUND")
            return
    if session is None:
        session = await create_session(db, user.id, _auto_title(question))
        await db.commit()

    # 1) 落用户消息
    user_msg = ChatMessage(
        session_id=session.id,
        role="user",
        content=question,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user_msg)
    session.msg_count = (session.msg_count or 0) + 1
    session.last_msg_at = datetime.now(timezone.utc)
    if session.title == "新对话":
        session.title = _auto_title(question)
    await db.flush()
    await db.commit()

    # 常问按频次自动累积（失败不影响问数，属于旁路增强）
    try:
        from .quick_question_service import record as record_qq

        await record_qq(db, user.id, question)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("记录常问失败：%s", exc)

    yield ev.meta_event(
        message_id=user_msg.id,
        session_id=session.id,
        title=session.title,
        trace_id=trace_id,
        # role 用于前端区分「用户消息 id」与「AI 回答 id」两次 meta 事件
        role="user",
    )

    # 2) 取历史
    hist_rows = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session.id, ChatMessage.id < user_msg.id)
            .order_by(ChatMessage.id)
        )
    ).scalars().all()
    history = _build_history(hist_rows)
    prev_sql = _last_sql(hist_rows)

    # 3) 缓存命中则直接回放，整段跳过 LLM
    #    只缓存首轮问题：多轮追问的语义依赖上文，
    #    「那北京呢」在不同上下文里含义不同，缓存会导致误命中
    unit_codes = _visible_units(user)
    cache_key = qa_cache.build_key(question, source_ids, unit_codes) if not history else ""
    # 缓存只在首轮生效（追问语义依赖上文），这里必须留痕：
    # 「改了语义层但结果没变」类问题，第一嫌疑就是命中了旧缓存。
    log_kv(
        logger, logging.DEBUG, "问数缓存判定",
        question=question, session_id=session.id,
        history_turns=len(hist_rows) // 2, cached=bool(cache_key),
        cache_key=cache_key or None,
        skip_reason="多轮追问不缓存" if not cache_key else None,
        unit_codes=unit_codes,
    )
    if cache_key:
        hit = qa_cache.get(cache_key)
        if hit:
            logger.info("问数缓存命中：%s", question[:40])
            async for event in _replay_cached(
                db, session, hit, question, user_msg.id, trace_id
            ):
                yield event
            return

    # 4) 跑 Agent
    final: Any = None
    try:
        async with readonly_session() as ro:
            runtime = AgentRuntime(db, ro, user, session_id=session.id)
            async for event, acc in runtime.run(
                question,
                source_ids=source_ids,
                history=history,
                prev_sql=prev_sql,
                reset_context=(session.msg_count or 0) <= 2,
            ):
                final = acc
                yield event
    except Exception as exc:  # noqa: BLE001
        logger.exception("问数流程异常：%s", exc)
        yield ev.error_event("INTERNAL", f"问数失败：{exc}")
        yield ev.done_event(error=str(exc))
        return

    if final is None:
        return

    # 4) 落助手消息
    payload = {
        "steps": final.steps,
        "sql": final.sql,
        "data_sources": final.data_sources,
        "tables": [{
            "columns": final.columns,
            "rows": final.rows[:200],
            "total": final.total,
            "truncated": final.truncated,
        }] if final.columns else [],
        "charts": [final.chart] if final.chart else [],
        "followups": final.followups,
        "degraded": final.degraded,
        "model_fallback": final.model_fallback,
        "rewritten": final.rewritten,
        "slots": final.slots,
        "clarify": final.clarify,
    }

    model_name = final.model
    if final.degraded:
        model_name = f"{model_name}(降级)"
    elif final.model_fallback:
        # 与「降级」区分：降级是压根没有可用模型，备用模型是有模型但首选的输出不合规
        model_name = f"{model_name}(备用)"

    ai_msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=final.content,
        payload=payload,
        rewritten_query=question,
        intent=final.intent,
        model=model_name,
        prompt_tokens=final.tokens.get("prompt", 0) if final.tokens else 0,
        completion_tokens=final.tokens.get("completion", 0) if final.tokens else 0,
        cost_ms=final.cost_ms,
        trace_id=trace_id,
        error=final.error or None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(ai_msg)
    session.msg_count = (session.msg_count or 0) + 1
    session.last_msg_at = datetime.now(timezone.utc)
    await db.flush()
    await db.commit()

    yield ev.meta_event(
        message_id=ai_msg.id,
        session_id=session.id,
        trace_id=trace_id,
        role="assistant",
    )

    # 5) 写入缓存：只缓存成功的首轮问题
    #    刻意**不缓存 0 行结果**——0 行往往是模型没理解对（维度取值猜错、
    #    选错了表），缓存它会让这个错误固化整个 TTL，后续同样的问题一直错，
    #    而且错得毫无征兆（耗时 20ms，看起来像是"很快查到了"）。
    if (
        cache_key
        and not final.error
        and not final.degraded
        and final.total > 0
    ):
        qa_cache.put(
            cache_key,
            {
                "steps": final.steps,
                "sql": final.sql,
                "data_sources": final.data_sources,
                "tables": payload["tables"],
                "charts": payload["charts"],
                "content": final.content,
                "followups": final.followups,
                "rewritten": final.rewritten,
                "intent": final.intent,
                "model": model_name,
                "model_fallback": final.model_fallback,
                "tokens": final.tokens or {},
            },
        )


def _auto_title(question: str) -> str:
    q = (question or "").strip()
    return q[:20] + ("..." if len(q) > 20 else "")


# ── 反馈 ────────────────────────────────────────────────────────────
async def submit_data_error(
    db: AsyncSession, user: CurrentUser, message_id: int, comment: str = ""
) -> int:
    """「数据有误」→ 生成反馈单，进入「反馈管理 ▸ 回复校对」。"""
    res = await db.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    msg = res.scalar_one_or_none()
    if not msg:
        raise NotFoundError("消息不存在")

    # 找到同一会话中该回答之前最近的用户提问
    qres = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == msg.session_id,
            ChatMessage.role == "user",
            ChatMessage.id < msg.id,
        )
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )
    question_msg = qres.scalar_one_or_none()

    fb = QaFeedback(
        question=question_msg.content if question_msg else "",
        user_id=user.id,
        username=user.username,
        ai_reply=(msg.content or "")[:2000],
        session_id=msg.session_id,
        message_id=message_id,
        status="待处理",
        remark=comment,
        created_at=datetime.now(timezone.utc),
    )
    db.add(fb)
    await db.flush()
    await db.commit()
    return fb.id
