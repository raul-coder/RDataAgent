"""快捷提问服务（T4-4）：常问 / 推荐 / 收藏。

三个分类共用一张表，靠 ``category`` + ``user_id`` 区分：
    recent     —— 常问，**系统自动累积**：每次提问都记一笔并 hit_count+1，
                  只有达到应用配置「常问设置」阈值的才会出现在面板上；
    recommend  —— 推荐，系统预置（user_id 为空），对所有人生效；
    favorite   —— 收藏，用户主动星标，提问原文可能很长，因此按原文精确去重。
"""

from __future__ import annotations

import re
from typing import List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import QuickQuestion
from . import config_service

RECENT = "recent"
RECOMMEND = "recommend"
FAVORITE = "favorite"
CATEGORIES = (RECENT, RECOMMEND, FAVORITE)

# 系统预置推荐问题（造数阶段写入；与业务语义层口径一致，确保都能问出数）
DEFAULT_RECOMMEND: List[str] = [
    "2026年各经营单元收入排名",
    "北京代表处今年达成情况",
    "各产品线收入占比",
    "2026年每月的合同金额趋势",
    "高风险项目有哪些",
    "各行业的合同额对比",
]


def _vo(r: QuickQuestion) -> dict:
    return {
        "id": r.id,
        "question": r.question,
        "category": r.category,
        "hit_count": r.hit_count,
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else "",
    }


async def list_questions(
    db: AsyncSession, user_id: int, category: str = RECENT, limit: int = 20
) -> List[dict]:
    stmt = select(QuickQuestion)
    if category == RECOMMEND:
        # 推荐是全局的，不区分用户
        stmt = stmt.where(QuickQuestion.category == RECOMMEND, QuickQuestion.user_id.is_(None))
        stmt = stmt.order_by(QuickQuestion.hit_count.desc(), QuickQuestion.id)
    else:
        stmt = stmt.where(
            QuickQuestion.category == category, QuickQuestion.user_id == user_id
        )
        if category == RECENT:
            # 常问要达到频次阈值才展示，避免"只问过一次"也挤进面板
            if not await config_service.get_flag(db, "hotRecommend", True):
                return []
            threshold = await config_service.get_int(db, "hotThreshold", 3)
            stmt = stmt.where(QuickQuestion.hit_count >= threshold)
        stmt = stmt.order_by(QuickQuestion.hit_count.desc(), QuickQuestion.updated_at.desc())

    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    return [_vo(r) for r in rows]


async def list_all_tabs(db: AsyncSession, user_id: int) -> dict:
    """一次拿三个 Tab 的数据，省掉前端三次往返。"""
    return {
        RECENT: await list_questions(db, user_id, RECENT, limit=8),
        RECOMMEND: await list_questions(db, user_id, RECOMMEND, limit=8),
        FAVORITE: await list_questions(db, user_id, FAVORITE, limit=20),
    }


async def add(
    db: AsyncSession, user_id: int, question: str, category: str = FAVORITE
) -> int:
    """新增（默认收藏）。已存在则直接返回原 id，不重复写入。"""
    q = (question or "").strip()[:255]
    if not q:
        return 0
    row = (
        await db.execute(
            select(QuickQuestion).where(
                QuickQuestion.user_id == user_id,
                QuickQuestion.question == q,
                QuickQuestion.category == category,
            )
        )
    ).scalar_one_or_none()
    if row:
        return row.id
    row = QuickQuestion(user_id=user_id, question=q, category=category, hit_count=0)
    db.add(row)
    await db.flush()
    return row.id


async def remove(db: AsyncSession, user_id: int, qid: int) -> bool:
    row = (
        await db.execute(select(QuickQuestion).where(QuickQuestion.id == qid))
    ).scalar_one_or_none()
    if row is None:
        return False
    # 推荐是全局数据，普通用户无权删除
    if row.category == RECOMMEND and row.user_id is not None:
        return False
    await db.delete(row)
    await db.flush()
    return True


# 「结果二次加工」类追问（排序/换图/导出/看前 N 个）只在当前上下文里成立，
# 单独放进「常问」面板毫无意义，因此不纳入频次统计。
# 与 intent.OPS_PATTERNS 保持一致，这里内联一份避免 services 层依赖 agent 层。
_OPS_PATTERNS = (
    re.compile(r"排序|升序|降序|倒序|从大到小|从小到大|反过来"),
    re.compile(r"换成|改成|改一下|用饼图|用饼状|用柱状|用条形|用折线|用曲线|画个|画一张|图表"),
    re.compile(r"导出|下载|存成|生成\s*(excel|csv)"),
    re.compile(r"只看前|显示前|取前|前\s*\d+\s*个"),
)


def _is_result_op(question: str) -> bool:
    return any(p.search(question) for p in _OPS_PATTERNS)


# 指代型追问：「那北京呢」「它同比呢」这类短句靠上文补全省略成分，
# 单独点进去会查出一个莫名其妙的结果，因此也不纳入常问。
_PRONOUN_HEAD = re.compile(r"^(这|那|它|他|她|其|上面|刚才|刚刚)")


def _is_context_dependent(question: str) -> bool:
    return len(question) <= 12 and bool(_PRONOUN_HEAD.match(question))


async def record(db: AsyncSession, user_id: int, question: str) -> None:
    """提问落库后调用：常问按频次自动累积。"""
    q = (question or "").strip()[:255]
    if not q or not user_id:
        return
    if _is_result_op(q) or _is_context_dependent(q):
        return
    row = (
        await db.execute(
            select(QuickQuestion).where(
                QuickQuestion.user_id == user_id,
                QuickQuestion.question == q,
                QuickQuestion.category == RECENT,
            )
        )
    ).scalar_one_or_none()
    if row:
        row.hit_count += 1
        row.updated_at = func.now()
    else:
        db.add(QuickQuestion(user_id=user_id, question=q, category=RECENT, hit_count=1))
    await db.flush()


async def seed_recommend(db: AsyncSession) -> int:
    """写入系统预置推荐问题（幂等）。"""
    existing = {
        r[0]
        for r in (
            await db.execute(
                select(QuickQuestion.question).where(
                    QuickQuestion.category == RECOMMEND, QuickQuestion.user_id.is_(None)
                )
            )
        ).all()
    }
    created = 0
    for i, q in enumerate(DEFAULT_RECOMMEND):
        if q in existing:
            continue
        db.add(
            QuickQuestion(
                user_id=None, question=q, category=RECOMMEND, hit_count=len(DEFAULT_RECOMMEND) - i
            )
        )
        created += 1
    if created:
        await db.flush()
    return created
