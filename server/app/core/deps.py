"""依赖注入：当前用户、权限校验。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..models import SysUser
from .config import settings
from .exceptions import ForbiddenError, UnauthorizedError
from .logging import get_logger
from .redis import get as redis_get
from .security import decode_token

logger = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

SUPER_ADMIN_CODE = "SUPER_ADMIN"


@dataclass
class CurrentUser:
    id: int
    username: str
    nickname: str = ""
    phone: str = ""
    email: str = ""
    avatar: str = ""
    status: int = 1
    pwd_must_change: bool = False
    role_ids: List[int] = field(default_factory=list)
    role_codes: List[str] = field(default_factory=list)
    perms: List[str] = field(default_factory=list)
    menus: List[dict] = field(default_factory=list)
    data_perms: dict = field(default_factory=dict)

    @property
    def is_superadmin(self) -> bool:
        return SUPER_ADMIN_CODE in self.role_codes

    def has(self, code: str) -> bool:
        return self.is_superadmin or code in self.perms


def _build_current_user(user: SysUser, perm_ctx: dict) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        username=user.username,
        nickname=user.nickname or "",
        phone=user.phone or "",
        email=user.email or "",
        avatar=user.avatar or "",
        status=user.status,
        pwd_must_change=bool(user.pwd_must_change),
        role_ids=perm_ctx.get("role_ids", []),
        role_codes=perm_ctx.get("role_codes", []),
        perms=perm_ctx.get("perms", []),
        menus=perm_ctx.get("menus", []),
        data_perms=perm_ctx.get("data_perms", {}),
    )


async def _resolve(
    db: AsyncSession, token: str
) -> Optional[CurrentUser]:
    """解析并校验 access token，返回 None 表示无效。"""
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    # 登出后 token 进入黑名单
    if redis_get(f"deny:{token[-16:]}"):
        return None

    result = await db.execute(
        select(SysUser).where(SysUser.id == user_id, SysUser.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None or user.status != 1:
        return None

    from ..services.perm_service import load_user_permissions

    perm_ctx = await load_user_permissions(db, user_id)
    return _build_current_user(user, perm_ctx)


async def get_current_user(
    request: Request,
    cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """必须登录，否则 401。"""
    if not cred:
        raise UnauthorizedError("未提供访问令牌")

    user = await _resolve(db, cred.credentials)
    if user is None:
        raise UnauthorizedError("令牌无效、已过期或账号不可用")

    path = request.url.path
    if user.pwd_must_change and not (
        path.startswith(f"{settings.API_PREFIX}/auth/") or path.endswith("/me")
    ):
        # 初始密码 / 重置后的密码必须先修改：仅放行认证相关接口与个人信息的读取
        raise ForbiddenError("请先修改初始密码")

    return user


async def get_current_user_optional(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[CurrentUser]:
    """可选鉴权：失败返回 None。"""
    if not cred:
        return None
    return await _resolve(db, cred.credentials)


def require_perm(code: str):
    """接口级权限校验（超级管理员放行）。"""

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has(code):
            logger.warning("权限拒绝 user=%s 需要=%s", user.username, code)
            raise ForbiddenError(f"缺少权限：{code}")
        return user

    return _dep


def require_any(*codes: str):
    """任一权限满足即可。"""

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not any(user.has(c) for c in codes):
            raise ForbiddenError(f"缺少权限：{' / '.join(codes)}")
        return user

    return _dep
