"""反馈闭环接口（T4-3）：点赞点踩 → 数据有误 → 回复校对处理。

两张表分工：
    chat_message_feedback —— 轻量态度（点赞/点踩），一条消息一条，可覆盖更新；
    qa_feedback           —— 正式反馈单（"数据有误"），进入管理员的回复校对队列。

越权防护：态度反馈只能对自己会话里的消息提交。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, get_current_user, require_perm
from ...core.exceptions import ForbiddenError, NotFoundError
from ...core.response import ok, paged
from ...db.session import get_db
from ...models import ChatMessage, ChatMessageFeedback, ChatSession, QaFeedback
from ...services import log_service

router = APIRouter(prefix="/feedback", tags=["feedback"])

STATUS_TODO = "待处理"
STATUS_DONE = "已处理"
VALID_STATUS = (STATUS_TODO, STATUS_DONE, "处理中", "已忽略")


class RatingIn(BaseModel):
    message_id: int
    rating: str                 # up / down
    comment: str = ""


class HandleIn(BaseModel):
    status: str = STATUS_DONE
    remark: str = ""


def _vo(r: QaFeedback) -> dict:
    return {
        "id": r.id,
        "question": r.question,
        "user_id": r.user_id,
        "username": r.username or "",
        "ai_reply": r.ai_reply or "",
        "session_id": r.session_id,
        "message_id": r.message_id,
        "status": r.status,
        "remark": r.remark or "",
        "handled_by": r.handled_by,
        "handled_at": r.handled_at.strftime("%Y-%m-%d %H:%M:%S") if r.handled_at else "",
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
    }


@router.post("/rating", summary="对回答点赞 / 点踩")
async def rate_message(
    req: RatingIn,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    if req.rating not in ("up", "down"):
        return ok({"ignored": True}, message="rating 只能是 up / down")

    msg = (
        await db.execute(select(ChatMessage).where(ChatMessage.id == req.message_id))
    ).scalar_one_or_none()
    if msg is None:
        raise NotFoundError(f"消息 {req.message_id} 不存在")

    session = (
        await db.execute(select(ChatSession).where(ChatSession.id == msg.session_id))
    ).scalar_one_or_none()
    if session is None or (session.user_id != current.id and not current.is_superadmin):
        raise ForbiddenError("只能对自己会话中的回答进行反馈")

    # 一条消息只保留最新态度：先删旧记录再插入，语义等价于 upsert
    existing = (
        await db.execute(
            select(ChatMessageFeedback).where(
                ChatMessageFeedback.message_id == req.message_id,
                ChatMessageFeedback.user_id == current.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.rating = req.rating
        existing.comment = req.comment
    else:
        db.add(
            ChatMessageFeedback(
                message_id=req.message_id,
                session_id=msg.session_id,
                user_id=current.id,
                rating=req.rating,
                comment=req.comment,
            )
        )
    await db.commit()
    return ok({"message_id": req.message_id, "rating": req.rating})


@router.get("", summary="回复校对列表（待处理优先）")
async def list_feedback(
    status: str = Query("", description="待处理 / 已处理 / 处理中 / 已忽略"),
    keyword: str = Query("", description="匹配问题或用户名"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("fb:review:view")),
):
    stmt = select(QaFeedback)
    if status:
        stmt = stmt.where(QaFeedback.status == status)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(QaFeedback.question.ilike(like), QaFeedback.username.ilike(like)))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            # 待处理排前面，其次按时间倒序，避免老工单被积压淹没
            stmt.order_by(
                (QaFeedback.status == STATUS_DONE).asc(), QaFeedback.created_at.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return paged([_vo(r) for r in rows], total, page, page_size)


@router.get("/stats", summary="反馈单统计（页面角标）")
async def feedback_stats(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("fb:review:view")),
):
    rows = (
        await db.execute(select(QaFeedback.status, func.count()).group_by(QaFeedback.status))
    ).all()
    counts = {r[0]: r[1] for r in rows}
    return ok(
        {
            "total": sum(counts.values()),
            "todo": counts.get(STATUS_TODO, 0),
            "done": counts.get(STATUS_DONE, 0),
            "by_status": counts,
        }
    )


@router.get("/{fb_id}", summary="反馈单详情")
async def get_feedback(
    fb_id: int,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("fb:review:view")),
):
    row = (
        await db.execute(select(QaFeedback).where(QaFeedback.id == fb_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"反馈单 {fb_id} 不存在")
    return ok(_vo(row))


@router.put("/{fb_id}", summary="处理反馈单")
async def handle_feedback(
    fb_id: int,
    req: HandleIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("fb:review:edit")),
):
    row = (
        await db.execute(select(QaFeedback).where(QaFeedback.id == fb_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"反馈单 {fb_id} 不存在")
    if req.status and req.status not in VALID_STATUS:
        raise ForbiddenError(f"状态非法，可选：{' / '.join(VALID_STATUS)}")

    row.status = req.status or STATUS_DONE
    row.remark = req.remark
    row.handled_by = current.id
    row.handled_at = func.now()

    # 同步会话维度标记，让问数日志能直接看到管理员结论
    if row.session_id:
        session = (
            await db.execute(select(ChatSession).where(ChatSession.id == row.session_id))
        ).scalar_one_or_none()
        if session:
            session.admin_feedback = row.status

    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"处理反馈单-{fb_id}→{row.status}",
        method="PUT /api/v1/feedback/{id}",
        ip=request.client.host if request.client else "",
        status="成功",
    )
    await db.commit()
    return ok({"id": fb_id, "status": row.status})


@router.get("/users/options", summary="反馈用户下拉选项")
async def feedback_user_options(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("fb:review:view")),
):
    """仅列出真正提交过反馈单的用户，避免下拉里塞满无关账号。"""
    rows = (
        await db.execute(
            select(QaFeedback.username)
            .where(QaFeedback.username.isnot(None), QaFeedback.username != "")
            .distinct()
            .order_by(QaFeedback.username)
        )
    ).scalars().all()
    return ok(list(rows))
