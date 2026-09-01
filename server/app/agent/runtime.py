"""问数编排（AgentRuntime）。

链路：
    意图识别 →（结果二次加工 / 直接回复 / 完整取数）
    完整取数：① 选择数据表&数据时效 → ② 推理逻辑（含多轮改写）
              → ③ 执行取数 SQL → ④ 展示取数结果 → ⑤ 执行结束

每一步都把真实数据通过 SSE 事件推送出去（对齐 demo 的 5 步可解释链路）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.deps import CurrentUser
from ..core.logging import get_logger, log_kv
from ..llm.router import build_providers
from ..models import SemDataSource, SysMenu
from . import context as ctx_store
from . import events as ev
from .nodes import (
    chart_advisor,
    compose,
    intent as intent_node,
    result_ops,
    sql_execute,
    sql_generate,
    sql_validate,
)
from .nodes.retrieve import SchemaContext, retrieve_schema, source_names
from .nodes.rewrite import rewrite
from .slots import Slots, describe, merge, to_prompt_hint

logger = get_logger(__name__)

MAX_RETRY = 2
AI_QA_MENU_ID = 1  # 智能问数（sys_menu.id）


@dataclass
class RunResult:
    """一次问数的完整结果（用于持久化）。"""

    sql: str = ""
    thought: str = ""
    intent: str = "data_query"
    model: str = ""
    tokens: dict = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    total: int = 0
    truncated: bool = False
    chart: dict = field(default_factory=dict)
    content: str = ""
    followups: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)
    cost_ms: int = 0
    error: str = ""
    degraded: bool = False
    rewritten: str = ""
    slots: dict = field(default_factory=dict)
    clarify: dict = field(default_factory=dict)


class AgentRuntime:
    def __init__(
        self,
        db: AsyncSession,
        ro: AsyncSession,
        user: CurrentUser,
        session_id: int = 0,
    ) -> None:
        self.db = db
        self.ro = ro
        self.user = user
        self.session_id = session_id

    async def run(
        self,
        question: str,
        *,
        source_ids: Optional[list[int]] = None,
        history: str = "",
        prev_sql: str = "",
        reset_context: bool = False,
    ) -> AsyncIterator[tuple[ev.SSEEvent, RunResult]]:
        result = RunResult()
        t_start = time.perf_counter()

        ctx = ctx_store.load(self.session_id) if self.session_id else ctx_store.SessionContext(0)
        # 追问丢上下文时先看这条：turn_count 是否为 0（说明没载入到）、
        # active_slots 是否带着上一轮的 time_range / metrics。
        log_kv(
            logger, logging.DEBUG, "会话上下文载入",
            session_id=self.session_id, turn_count=ctx.turn_count,
            active_slots=ctx.active_slots.to_dict(),
            has_last_result=bool(ctx.last_result_key), last_sql=ctx.last_sql,
            reset_context=reset_context,
        )
        if reset_context:
            ctx.active_slots = Slots()
            ctx.last_result_key = None

        providers = await build_providers(self.db)
        result.degraded = not any(getattr(p, "is_remote", False) for p in providers)
        result.model = providers[0].__class__.__name__

        yield ev.meta_event(
            question=question,
            model=result.model,
            degraded=result.degraded,
            data_as_of=settings.DATA_AS_OF,
            session_id=self.session_id,
        ), result

        # ── 意图识别 ──────────────────────────────────────────────
        intent = intent_node.classify(question)
        result.intent = intent.intent
        yield ev.intent_event(intent.intent, intent.op), result

        # 闲聊 / 越界：直接回复，不查库
        if intent.intent in {"chitchat", "out_of_scope"}:
            result.content = intent.reply
            for i in range(0, len(intent.reply), 24):
                yield ev.token_event(intent.reply[i : i + 24]), result
            result.cost_ms = int((time.perf_counter() - t_start) * 1000)
            yield ev.done_event(rows=0, cost_ms=result.cost_ms, degraded=result.degraded), result
            return

        schema = await retrieve_schema(self.db, source_ids)

        # ── 结果二次加工：复用上轮结果集，不重跑 SQL ───────────────
        if intent.intent == "result_ops":
            cached = ctx_store.load_result(ctx.last_result_key) if ctx.last_result_key else None
            if not cached:
                # 常见诱因：Redis 重启/过期，或（多副本时）请求打到了另一个进程
                log_kv(logger, logging.WARNING, "二次加工无结果集可用",
                       session_id=self.session_id, op=intent.op,
                       last_result_key=ctx.last_result_key, question=question)
                fallback = "上一轮没有可加工的结果，请重新提问一次后再试。"
                result.content = fallback
                for i in range(0, len(fallback), 24):
                    yield ev.token_event(fallback[i : i + 24]), result
                yield ev.done_event(rows=0, degraded=result.degraded), result
                return

            t = time.perf_counter()
            new_payload, note = result_ops.apply(cached, intent.op or "sort", question)
            ctx_store.cache_result(self.session_id, new_payload)
            ctx.last_result_key = ctx_store.cache_result(self.session_id, new_payload)

            result.columns = new_payload.get("columns", [])
            result.rows = new_payload.get("rows", [])
            result.total = new_payload.get("total", len(result.rows))
            result.chart = new_payload.get("chart") or {}
            result.content = note
            result.cost_ms = int((time.perf_counter() - t_start) * 1000)

            yield ev.result_op_event(intent.op or "", note), result
            yield ev.table_event(result.columns, result.rows[:200], result.total, False), result
            if result.chart:
                yield ev.chart_event(result.chart.get("option", {}), result.chart.get("type", "table")), result
            for i in range(0, len(note), 24):
                yield ev.token_event(note[i : i + 24]), result
            ctx_store.save(ctx)
            yield ev.done_event(rows=result.total, cost_ms=result.cost_ms, degraded=result.degraded), result
            return

        # ── ① 选择数据表 & 数据时效 ───────────────────────────────
        yield ev.step_event(1, ev.RUNNING), result
        t = time.perf_counter()
        result.data_sources = source_names(schema)
        allowed_tables = schema.allowed_tables
        calibers = [r["content"] for r in schema.rules if r["scene"] == "caliber"][:1]
        desc1 = (
            f"选用数据源：{'、'.join(result.data_sources)}\n"
            f"数据截止日期：{settings.DATA_AS_OF}"
            + (f"\n业务口径：{calibers[0]}" if calibers else "")
        )
        result.steps.append({"index": 1, "status": ev.DONE, "desc": desc1, "cost_ms": _ms(t)})
        yield ev.step_event(1, ev.DONE, desc1, _ms(t)), result

        # ── 多轮改写（指代消解 / 条件叠加 / 时间切换）─────────────
        rw = await rewrite(
            question,
            ctx.active_slots,
            schema,
            default_year=settings.DEFAULT_YEAR,
            providers=providers,
            history=history,
        )
        result.rewritten = rw.rewritten
        result.slots = rw.merged.to_dict()

        if rw.need_clarify:
            result.clarify = {"question": question, "options": rw.options, "reason": rw.reason}
            tip = "我还没确定你想看什么，请选择或直接补充：\n- " + "\n- ".join(rw.options[:5])
            result.content = tip
            yield ev.clarify_event(question, rw.options, rw.reason), result
            for i in range(0, len(tip), 24):
                yield ev.token_event(tip[i : i + 24]), result
            yield ev.done_event(rows=0, degraded=result.degraded), result
            return

        if rw.rewritten != question:
            yield ev.slots_event(describe(rw.merged, metric_names={m["code"]: m["name"] for m in schema.metrics}), rw.merged.to_dict()), result

        # ── ② 推理逻辑：生成 SQL ──────────────────────────────────
        yield ev.step_event(2, ev.RUNNING), result
        t = time.perf_counter()
        slot_hint = to_prompt_hint(
            rw.merged, metric_names={m["code"]: m["name"] for m in schema.metrics}
        )
        draft = await sql_generate.generate_sql(
            self.db,
            providers,
            rw.rewritten,
            schema,
            history=history,
            prev_sql=prev_sql or ctx.last_sql,
            slot_hint=slot_hint,
            intent=intent.intent,
        )
        result.thought = draft.thought
        result.model = draft.model or result.model
        result.tokens = draft.tokens or {}
        result.steps.append({"index": 2, "status": ev.DONE, "desc": draft.thought, "cost_ms": _ms(t)})
        yield ev.step_event(2, ev.DONE, draft.thought, _ms(t)), result

        # ── ③ 校验 + 执行（含自愈重试）────────────────────────────
        unit_codes = self._visible_units()
        yield ev.step_event(3, ev.RUNNING), result
        t = time.perf_counter()
        final_sql = ""
        exec_result: Optional[sql_execute.QueryResult] = None

        for attempt in range(MAX_RETRY + 1):
            try:
                final_sql = sql_validate.validate(
                    draft.sql,
                    allowed_tables,
                    unit_codes,
                    is_ranking=intent.intent == "ranking"
                    or sql_validate.looks_like_ranking(rw.rewritten),
                )
                result.sql = final_sql
                yield ev.sql_event(final_sql, result.data_sources), result
                exec_result = await sql_execute.execute_sql(self.ro, final_sql)
                break
            except sql_validate.SQLRejectedError as exc:
                result.error = f"SQL_REJECTED: {exc}"
                result.steps.append({"index": 3, "status": ev.FAIL, "desc": str(exc), "cost_ms": _ms(t)})
                yield ev.step_event(3, ev.FAIL, str(exc), _ms(t)), result
                yield ev.error_event("SQL_REJECTED", str(exc), attempt), result
                yield ev.done_event(error=result.error), result
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("SQL 执行失败（第 %d/%d 次）：%s", attempt + 1, MAX_RETRY, exc)
                if attempt >= MAX_RETRY:
                    result.error = f"SQL_EXEC_ERROR: {exc}"
                    result.steps.append({"index": 3, "status": ev.FAIL, "desc": str(exc), "cost_ms": _ms(t)})
                    yield ev.step_event(3, ev.FAIL, str(exc), _ms(t)), result
                    yield ev.error_event("SQL_EXEC_ERROR", str(exc), MAX_RETRY), result
                    yield ev.done_event(error=result.error), result
                    return
                yield ev.error_event("SQL_RETRY", str(exc), attempt + 1), result
                draft = await sql_generate.generate_sql(
                    self.db,
                    providers,
                    rw.rewritten,
                    schema,
                    history=history,
                    prev_sql=draft.sql,
                    slot_hint=slot_hint,
                    intent=intent.intent,
                    retry_hint=f"上一次 SQL 执行报错：{exc}\n原 SQL：{draft.sql}\n请修正后重新输出。",
                )

        result.steps.append({"index": 3, "status": ev.DONE, "desc": final_sql, "cost_ms": _ms(t)})
        yield ev.step_event(3, ev.DONE, final_sql, _ms(t)), result

        # ── ④ 展示取数结果 ────────────────────────────────────────
        yield ev.step_event(4, ev.RUNNING), result
        t = time.perf_counter()
        assert exec_result is not None
        result.columns = exec_result.columns
        result.rows = exec_result.rows
        result.total = exec_result.total
        result.truncated = exec_result.truncated

        yield ev.table_event(
            exec_result.columns, exec_result.rows[:200], exec_result.total, exec_result.truncated
        ), result

        chart = chart_advisor.advise(exec_result.columns, exec_result.rows, hint=draft.chart)
        result.chart = chart
        yield ev.chart_event(chart.get("option", {}), chart.get("type", "table")), result

        stats = sql_execute.summarize(exec_result)
        desc4 = (
            f"返回 {exec_result.total} 行"
            + ("（已截断）" if exec_result.truncated else "")
            + f"，耗时 {exec_result.cost_ms}ms"
        )
        result.steps.append({"index": 4, "status": ev.DONE, "desc": desc4, "cost_ms": _ms(t)})
        yield ev.step_event(4, ev.DONE, desc4, _ms(t)), result

        # ── ⑤ 结论流式生成 ────────────────────────────────────────
        yield ev.step_event(5, ev.RUNNING), result
        t = time.perf_counter()
        text_parts: list[str] = []
        perm_note = await self._permission_note(unit_codes)
        try:
            async for delta in compose.stream_answer(
                providers,
                rw.rewritten,
                compose.build_context(stats, exec_result.columns, exec_result.rows),
                permission_note=perm_note,
            ):
                text_parts.append(delta)
                yield ev.token_event(delta), result
        except Exception as exc:  # noqa: BLE001
            logger.warning("结论生成失败，回退程序化摘要：%s", exc)
            log_kv(logger, logging.WARNING, "结论生成降级为程序化摘要",
                   model=result.model, rows=exec_result.total, error=str(exc))
            fallback = compose.fallback_answer(
                rw.rewritten, exec_result.columns, exec_result.rows, stats,
                permission_note=perm_note,
            )
            for i in range(0, len(fallback), 24):
                piece = fallback[i : i + 24]
                text_parts.append(piece)
                yield ev.token_event(piece), result
            result.degraded = True

        result.content = "".join(text_parts)
        result.followups = compose.suggest_followups(rw.rewritten, exec_result.columns)
        result.cost_ms = int((time.perf_counter() - t_start) * 1000)

        # ── 更新会话上下文（供下一轮多轮追问）─────────────────────
        ctx.active_slots = rw.merged
        ctx.last_sql = final_sql
        ctx.last_result_key = ctx_store.cache_result(
            self.session_id,
            {
                "columns": result.columns,
                "rows": result.rows,
                "total": result.total,
                "truncated": result.truncated,
                "chart": result.chart,
                "question": rw.rewritten,
                "sql": final_sql,
            },
        )
        ctx.turn_count += 1
        ctx.summary = _update_summary(ctx.summary, question, result.content, exec_result.total)
        ctx_store.save(ctx)
        # 下一轮能不能继承到条件，取决于这里写进去的槽位是否正确
        log_kv(
            logger, logging.DEBUG, "会话上下文已回写",
            session_id=self.session_id, turn_count=ctx.turn_count,
            active_slots=ctx.active_slots.to_dict(),
            last_result_key=ctx.last_result_key, sql=final_sql,
        )

        yield ev.followups_event(result.followups), result
        result.steps.append({"index": 5, "status": ev.DONE, "desc": f"全流程耗时 {result.cost_ms}ms", "cost_ms": _ms(t)})
        yield ev.step_event(5, ev.DONE, f"全流程耗时 {result.cost_ms}ms", _ms(t)), result
        yield ev.done_event(
            tokens=result.tokens,
            model=result.model,
            cost_ms=result.cost_ms,
            degraded=result.degraded,
            rows=result.total,
            rewritten=result.rewritten,
        ), result

    def _visible_units(self) -> Optional[list[str]]:
        if self.user.is_superadmin:
            return None
        raw = self.user.data_perms.get(str(AI_QA_MENU_ID))
        if raw is None:
            return None
        return list(raw) if raw else None

    async def _permission_note(self, unit_codes: Optional[list[str]]) -> str:
        """把行级权限翻译成结论里的人话。

        为什么要显式提示（UC-3 验收要求）：权限过滤是服务端强制注入的，
        用户看不到 SQL。若不提示，查「北京代表处」返回 0 行时，
        用户会以为数据不存在或自己写错了名字，而不是"我没有这个权限"。
        """
        if not unit_codes:
            return ""
        names: list[str] = []
        try:
            rows = (
                await self.ro.execute(
                    text(
                        "SELECT unit_name FROM bi.dim_unit "
                        "WHERE unit_code = ANY(:codes) ORDER BY unit_code"
                    ),
                    {"codes": list(unit_codes)},
                )
            ).scalars().all()
            names = [str(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            # 名称查不到不影响主链路，退化为展示编码
            logger.warning("查询经营单元名称失败，退化为编码展示：%s", exc)

        shown = "、".join(names) if names else "、".join(unit_codes)
        return f"已按您的数据权限范围过滤（仅含：{shown}）"


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _update_summary(prev: str, question: str, answer: str, rows: int) -> str:
    """增量维护长对话摘要（超出阈值后用它替代原始历史，避免上下文膨胀）。"""
    line = f"问：{question[:40]} → 答（{rows}行）：{answer[:60]}"
    lines = [l for l in (prev or "").split("\n") if l]
    lines.append(line)
    return "\n".join(lines[-8:])


async def allowed_source_ids(db: AsyncSession) -> list[int]:
    rows = (await db.execute(
        select(SemDataSource.id).where(SemDataSource.enabled.is_(True))
    )).scalars().all()
    return list(rows)


async def ai_qa_menu_id(db: AsyncSession) -> int:
    res = await db.execute(select(SysMenu.id).where(SysMenu.perm_code == "ai:qa"))
    mid = res.scalar_one_or_none()
    return int(mid) if mid else AI_QA_MENU_ID
