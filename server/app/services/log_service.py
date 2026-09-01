"""操作日志服务：落库 + 查询。"""

from __future__ import annotations

from typing import List, Optional

from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SysOperLog

# 单次导出的行数上限（防止误操作把整表读进内存）
EXPORT_MAX_ROWS = 50000


def _parse_dt(value: str, end_of_day: bool = False):
    """接受 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM:SS'；解析失败抛 ValueError。"""
    text = value.strip()
    fmt = "%Y-%m-%d %H:%M:%S" if len(text) > 10 else "%Y-%m-%d"
    dt = datetime.strptime(text, fmt)
    if end_of_day and len(text) == 10:
        # 只给日期时，结束时间应包含当天 23:59:59，否则当天数据会被漏掉
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


async def record(
    db: AsyncSession,
    action: str,
    status: str = "成功",
    log_type: str = "oper",
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    method: str = "",
    ip: str = "",
    user_agent: str = "",
    cost_ms: Optional[int] = None,
) -> None:
    """写入一条操作日志（不提交事务，由调用方统一 commit）。"""
    db.add(
        SysOperLog(
            user_id=user_id,
            username=username or "",
            log_type=log_type,
            action=action,
            method=method,
            ip=ip,
            user_agent=user_agent,
            status=status,
            cost_ms=cost_ms,
        )
    )
    await db.flush()


async def query(
    db: AsyncSession,
    keyword: str = "",
    username: str = "",
    log_type: str = "",
    status: str = "",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    stmt: Select = select(SysOperLog)
    if keyword:
        stmt = stmt.where(SysOperLog.action.ilike(f"%{keyword}%"))
    if username:
        stmt = stmt.where(SysOperLog.username.ilike(f"%{username}%"))
    if log_type:
        stmt = stmt.where(SysOperLog.log_type == log_type)
    if status:
        stmt = stmt.where(SysOperLog.status == status)
    # 时间范围：只传 start 表示"从某时起"，只传 end 表示"截至某时"
    if start_time:
        stmt = stmt.where(SysOperLog.created_at >= _parse_dt(start_time))
    if end_time:
        stmt = stmt.where(SysOperLog.created_at <= _parse_dt(end_time, end_of_day=True))

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(SysOperLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return {
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "username": r.username,
                "log_type": r.log_type,
                "action": r.action,
                "method": r.method,
                "ip": r.ip,
                "status": r.status,
                "cost_ms": r.cost_ms,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def export_rows(
    db: AsyncSession,
    keyword: str = "",
    username: str = "",
    log_type: str = "",
    status: str = "",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = EXPORT_MAX_ROWS,
) -> List[dict]:
    """按当前筛选条件导出；上限保护，避免误操作把整表读进内存。"""
    result = await query(
        db, keyword, username, log_type, status,
        start_time=start_time, end_time=end_time, page=1, page_size=limit,
    )
    return result["items"]
