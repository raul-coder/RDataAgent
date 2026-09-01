"""用户服务：CRUD / 启停 / 批量重置密码。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.exceptions import AppException, ErrorCode
from ..core.logging import get_logger
from ..core.security import hash_password
from ..models import SysRole, SysUser, SysUserRole
from . import perm_service

logger = get_logger(__name__)

DEFAULT_PASSWORD = "123456"


async def query_users(
    db: AsyncSession,
    keyword: str = "",
    role_id: Optional[int] = None,
    status: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    stmt = select(SysUser).where(SysUser.deleted_at.is_(None))
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(SysUser.username.ilike(like), SysUser.nickname.ilike(like), SysUser.phone.ilike(like))
        )
    if status is not None:
        stmt = stmt.where(SysUser.status == status)
    if role_id:
        stmt = stmt.join(SysUserRole, SysUserRole.user_id == SysUser.id).where(
            SysUserRole.role_id == role_id
        )

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(SysUser.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    role_map = await _roles_of(db, [u.id for u in rows])
    return {
        "items": [_vo(u, role_map.get(u.id, [])) for u in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def _roles_of(db: AsyncSession, user_ids: List[int]) -> dict:
    if not user_ids:
        return {}
    rows = (
        await db.execute(
            select(SysUserRole.user_id, SysRole.id, SysRole.code, SysRole.name)
            .join(SysRole, SysRole.id == SysUserRole.role_id)
            .where(SysUserRole.user_id.in_(user_ids))
        )
    ).all()
    out: dict = {}
    for uid, rid, rcode, rname in rows:
        out.setdefault(uid, []).append({"id": rid, "code": rcode, "name": rname})
    return out


def _vo(u: SysUser, roles: List[dict]) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "nickname": u.nickname or "",
        "phone": u.phone or "",
        "email": u.email or "",
        "status": u.status,
        "valid_until": u.valid_until.isoformat() if u.valid_until else "",
        "last_login_at": u.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if u.last_login_at else "",
        "last_login_ip": u.last_login_ip or "",
        "pwd_must_change": bool(u.pwd_must_change),
        "created_at": u.created_at.strftime("%Y-%m-%d %H:%M:%S") if u.created_at else "",
        "roles": roles,
        "role_name": roles[0]["name"] if roles else "",
    }


async def get_user(db: AsyncSession, user_id: int) -> Optional[SysUser]:
    res = await db.execute(
        select(SysUser).where(SysUser.id == user_id, SysUser.deleted_at.is_(None))
    )
    return res.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    username: str,
    role_ids: List[int],
    nickname: str = "",
    phone: str = "",
    email: str = "",
    valid_until: Optional[str] = None,
    password: str = DEFAULT_PASSWORD,
) -> SysUser:
    exists = (
        await db.execute(
            select(func.count()).select_from(SysUser).where(SysUser.username == username)
        )
    ).scalar_one()
    if exists:
        raise AppException(f"用户名 {username} 已存在", ErrorCode.CONFLICT, 409)

    user = SysUser(
        username=username,
        password_hash=hash_password(password),
        nickname=nickname or username,
        phone=phone,
        email=email,
        status=1,
        valid_until=datetime.fromisoformat(valid_until).date() if valid_until else None,
        pwd_must_change=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()

    for rid in role_ids or []:
        db.add(SysUserRole(user_id=user.id, role_id=rid))
    await db.flush()
    logger.info("创建用户 %s（角色 %s）", username, role_ids)
    return user


async def update_user(
    db: AsyncSession,
    user_id: int,
    nickname: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    valid_until: Optional[str] = None,
    role_ids: Optional[List[int]] = None,
) -> SysUser:
    user = await get_user(db, user_id)
    if user is None:
        raise AppException("用户不存在", ErrorCode.NOT_FOUND, 404)

    if nickname is not None:
        user.nickname = nickname
    if phone is not None:
        user.phone = phone
    if email is not None:
        user.email = email
    if valid_until is not None:
        user.valid_until = datetime.fromisoformat(valid_until).date() if valid_until else None
    user.updated_at = datetime.now(timezone.utc)
    await db.flush()

    if role_ids is not None:
        await db.execute(
            SysUserRole.__table__.delete().where(SysUserRole.user_id == user_id)
        )
        for rid in role_ids:
            db.add(SysUserRole(user_id=user_id, role_id=rid))
        await db.flush()
        perm_service.invalidate(user_id)

    return user


async def delete_user(db: AsyncSession, user_id: int) -> None:
    user = await get_user(db, user_id)
    if user is None:
        raise AppException("用户不存在", ErrorCode.NOT_FOUND, 404)
    if user.username == "admin":
        raise AppException("内置管理员账号不可删除", ErrorCode.CONFLICT, 409)
    user.deleted_at = datetime.now(timezone.utc)
    await db.flush()
    perm_service.invalidate(user_id)


async def toggle_status(db: AsyncSession, user_id: int) -> int:
    user = await get_user(db, user_id)
    if user is None:
        raise AppException("用户不存在", ErrorCode.NOT_FOUND, 404)
    user.status = 0 if user.status == 1 else 1
    user.updated_at = datetime.now(timezone.utc)
    await db.flush()
    perm_service.invalidate(user_id)
    return user.status


async def reset_password(
    db: AsyncSession, user_ids: List[int], password: str = DEFAULT_PASSWORD
) -> int:
    if not user_ids:
        raise AppException("请至少选择一个用户", ErrorCode.BAD_REQUEST)
    if len(password) < 6:
        raise AppException("重置密码至少 6 位", ErrorCode.BAD_REQUEST)

    rows = (
        await db.execute(select(SysUser).where(SysUser.id.in_(user_ids)))
    ).scalars().all()
    for u in rows:
        u.password_hash = hash_password(password)
        u.pwd_must_change = True
        u.updated_at = datetime.now(timezone.utc)
    await db.flush()
    for u in rows:
        perm_service.invalidate(u.id)
    logger.info("批量重置 %d 个用户的密码", len(rows))
    return len(rows)
