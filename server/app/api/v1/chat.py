"""智能问数接口：SSE 流式问数 + 会话管理 + 反馈。"""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, get_current_user
from ...core.exceptions import NotFoundError
from ...core.logging import get_logger, new_trace_id
from ...core.response import ok, paged
from ...db.session import get_db
from ...schemas.chat import (
    CompletionReq,
    DataErrorReq,
    SessionCreateReq,
    SessionPinReq,
    SessionRenameReq,
)
from ...services import chat_service

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/completions", summary="智能问数（SSE 流式）")
async def completions(
    req: CompletionReq,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """SSE 事件流：meta / step / sql / table / chart / token / followups / done / error。"""
    new_trace_id()

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for event in chat_service.stream_answer(
                db,
                user,
                req.content,
                session_id=req.session_id,
                source_ids=req.source_ids,
            ):
                yield event.encode().encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.exception("SSE 流异常：%s", exc)
            from ...agent import events as ev

            yield ev.error_event("INTERNAL", f"问数失败：{exc}").encode().encode("utf-8")
            yield ev.done_event(error=str(exc)).encode().encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，否则流式会攒批
        },
    )


# ── 会话 ────────────────────────────────────────────────────────────
@router.get("/sessions", summary="会话列表（置顶优先）")
async def sessions(
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    r = await chat_service.list_sessions(db, user.id, keyword, page, page_size)
    return paged(r["items"], r["total"], r["page"], r["page_size"])


@router.post("/sessions", summary="新建会话")
async def create_session(
    req: SessionCreateReq,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    s = await chat_service.create_session(db, user.id, req.title)
    await db.commit()
    return ok({"id": s.id, "title": s.title})


@router.get("/sessions/{session_id}", summary="会话详情（含消息）")
async def session_detail(
    session_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    s = await chat_service.get_session(db, user.id, session_id)
    if not s:
        # get_session 已按 user_id 过滤，查不到就是"别人的会话或不存在"。
        # 统一返回 404 而非 403：不泄露"这个 ID 存在但你没权限"这一信息，
        # 与重命名/删除的口径保持一致。
        raise NotFoundError("会话不存在")
    msgs = await chat_service.list_messages(db, user.id, session_id, page, page_size)
    return ok({**chat_service._session_vo(s), "messages": msgs["items"], "total": msgs["total"]})


@router.put("/sessions/{session_id}", summary="重命名会话")
async def rename(
    session_id: int,
    req: SessionRenameReq,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    await chat_service.rename_session(db, user.id, session_id, req.title)
    await db.commit()
    return ok({"id": session_id, "title": req.title})


@router.put("/sessions/{session_id}/pin", summary="置顶 / 取消置顶")
async def pin(
    session_id: int,
    req: SessionPinReq,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    await chat_service.pin_session(db, user.id, session_id, req.pinned)
    await db.commit()
    return ok({"id": session_id, "pinned": req.pinned})


@router.delete("/sessions/{session_id}", summary="删除会话")
async def remove(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    await chat_service.delete_session(db, user.id, session_id)
    await db.commit()
    return ok({"deleted": True})


# ── 反馈 ────────────────────────────────────────────────────────────
@router.post("/data-error", summary="提交「数据有误」反馈")
async def data_error(
    req: DataErrorReq,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    fid = await chat_service.submit_data_error(db, user, req.message_id, req.comment)
    return ok({"feedback_id": fid})


# ── 问数日志（会话维度，供「问数日志」tab 使用）─────────────────
@router.get("/logs", summary="问数日志")
async def chat_logs(
    days: int = Query(30, description="最近 N 天；0 表示全部"),
    username: str = Query(""),
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    from datetime import timedelta

    from sqlalchemy import func, select

    from ...models import ChatSession, SysUser

    # 数据边界：问数日志含"谁问了什么"，属于他人隐私。
    # 只有管理员（sys:log:view）能看全量，普通用户只看自己的。
    # 之前这里没有任何过滤，普通用户能翻到别人的会话标题与反馈状态。
    if not (user.is_superadmin or user.has("sys:log:view")):
        stmt = select(ChatSession, SysUser.username).join(
            SysUser, SysUser.id == ChatSession.user_id
        ).where(ChatSession.deleted_at.is_(None), ChatSession.user_id == user.id)
    else:
        stmt = select(ChatSession, SysUser.username).join(
            SysUser, SysUser.id == ChatSession.user_id
        ).where(ChatSession.deleted_at.is_(None))
    if days and days > 0:
        stmt = stmt.where(ChatSession.created_at >= func.now() - timedelta(days=days))
    if username:
        stmt = stmt.where(SysUser.username == username)
    if keyword:
        stmt = stmt.where(ChatSession.title.ilike(f"%{keyword}%"))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(ChatSession.last_msg_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = []
    for s, uname in rows:
        vo = chat_service._session_vo(s)
        vo["username"] = uname
        items.append(vo)
    return paged(items, total, page, page_size)
