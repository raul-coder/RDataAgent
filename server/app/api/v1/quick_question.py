"""快捷提问接口（T4-4）：常问 / 推荐 / 收藏三 Tab。

「常问」由系统按提问频次自动生成（见 quick_question_service.record），
用户只能增删自己的收藏；推荐项是全局预置数据，只读。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, get_current_user
from ...core.exceptions import ForbiddenError
from ...core.response import ok
from ...db.session import get_db
from ...services import quick_question_service as qq

router = APIRouter(prefix="/quick-questions", tags=["quick-questions"])


class QQIn(BaseModel):
    question: str
    category: str = qq.FAVORITE


@router.get("", summary="快捷提问（三 Tab）")
async def list_quick_questions(
    category: str = Query("", description="recent / recommend / favorite；留空返回三个 Tab"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    if not category:
        return ok(await qq.list_all_tabs(db, current.id))
    if category not in qq.CATEGORIES:
        raise ForbiddenError(f"category 只能是 {' / '.join(qq.CATEGORIES)}")
    return ok(await qq.list_questions(db, current.id, category, limit))


@router.post("", summary="新增快捷提问（默认收藏）")
async def add_quick_question(
    req: QQIn,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    if req.category not in (qq.FAVORITE, qq.RECENT):
        raise ForbiddenError("只能新增收藏或常问")
    qid = await qq.add(db, current.id, req.question, req.category)
    await db.commit()
    return ok({"id": qid})


@router.delete("/{qid}", summary="移除收藏 / 常问")
async def remove_quick_question(
    qid: int,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    removed = await qq.remove(db, current.id, qid)
    await db.commit()
    return ok({"removed": removed})
