"""认证服务：登录 / 刷新 / 登出 / 改密。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import captcha, rate_limit, redis
from ..core.config import settings
from ..core.exceptions import AppException, ErrorCode
from ..core.logging import get_logger
from ..core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from ..models import SysUser
from . import log_service, perm_service

logger = get_logger(__name__)

MAX_FAIL = 5
LOCK_SECONDS = 600
CAPTCHA_AFTER_FAIL = 3  # 连续失败 3 次后强制要求验证码


def _fail_key(username: str) -> str:
    return f"loginfail:{username}"


def _lock_key(username: str) -> str:
    return f"loginlock:{username}"


def is_locked(username: str) -> bool:
    return redis.get(_lock_key(username)) is not None


def need_captcha(username: str) -> bool:
    """连续失败达到阈值后要求验证码。"""
    if redis.is_degraded():
        return False
    return int(redis.get(_fail_key(username)) or 0) >= CAPTCHA_AFTER_FAIL


async def login(
    db: AsyncSession,
    username: str,
    password: str,
    request: Request,
    captcha_code: Optional[str] = None,
    captcha_id: Optional[str] = None,
) -> dict:
    ip = rate_limit.client_ip(request)
    ua = request.headers.get("User-Agent", "")[:512]

    # 1) 限流
    try:
        rate_limit.check_login(username, request)
    except AppException as exc:
        await log_service.record(
            db, username=username, log_type="login", action="用户登录系统",
            method="POST /api/v1/auth/login", ip=ip, user_agent=ua,
            status="失败-请求过于频繁",
        )
        raise exc

    # 2) 锁定检查
    if is_locked(username):
        await log_service.record(
            db, username=username, log_type="login", action="用户登录系统",
            method="POST /api/v1/auth/login", ip=ip, user_agent=ua,
            status="失败-账号锁定",
        )
        raise AppException(
            f"连续失败 {MAX_FAIL} 次，账号已锁定，请 10 分钟后重试",
            ErrorCode.UNAUTHORIZED, 401,
        )

    # 3) 验证码（失败达阈值后强制）
    if need_captcha(username):
        if not captcha.verify(captcha_id or "", captcha_code or ""):
            await log_service.record(
                db, username=username, log_type="login", action="用户登录系统",
                method="POST /api/v1/auth/login", ip=ip, user_agent=ua,
                status="失败-验证码错误",
            )
            raise AppException("验证码错误或已失效", ErrorCode.BAD_REQUEST, 400)

    # 4) 查用户
    result = await db.execute(
        select(SysUser).where(
            SysUser.username == username, SysUser.deleted_at.is_(None)
        )
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        fails = redis.incr(_fail_key(username), LOCK_SECONDS)
        if fails >= MAX_FAIL:
            redis.setex(_lock_key(username), LOCK_SECONDS, "1")
        await log_service.record(
            db, username=username, log_type="login", action="用户登录系统",
            method="POST /api/v1/auth/login", ip=ip, user_agent=ua,
            status="失败-用户名或密码错误",
        )
        raise AppException(
            f"用户名或密码错误（连续失败 {fails}/{MAX_FAIL} 次）",
            ErrorCode.UNAUTHORIZED, 401,
        )

    # 5) 状态校验
    if user.status != 1:
        await log_service.record(
            db, username=username, log_type="login", action="用户登录系统",
            method="POST /api/v1/auth/login", ip=ip, user_agent=ua, status="失败-账号禁用",
        )
        raise AppException("账号已被禁用", ErrorCode.FORBIDDEN, 403)
    if user.valid_until and user.valid_until < date.today():
        await log_service.record(
            db, username=username, log_type="login", action="用户登录系统",
            method="POST /api/v1/auth/login", ip=ip, user_agent=ua, status="失败-账号过期",
        )
        raise AppException("账号已过期", ErrorCode.FORBIDDEN, 403)

    # 6) 登录成功：清失败计数、更新登录信息
    redis.delete(_fail_key(username))
    redis.delete(_lock_key(username))
    user.last_login_at = datetime.now(timezone.utc)
    user.last_login_ip = ip
    await db.flush()

    perm_ctx = await perm_service.load_user_permissions(db, user.id)
    access = create_access_token(user.id, {"username": user.username})
    refresh = create_refresh_token(user.id)

    await log_service.record(
        db, user_id=user.id, username=username, log_type="login", action="用户登录系统",
        method="POST /api/v1/auth/login", ip=ip, user_agent=ua, status="成功",
    )

    logger.info("用户登录成功 user=%s ip=%s", username, ip)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname or "",
            "phone": user.phone or "",
            "email": user.email or "",
            "avatar": user.avatar or "",
            "status": user.status,
            "pwd_must_change": bool(user.pwd_must_change),
            "role_codes": perm_ctx.get("role_codes", []),
            "perms": perm_ctx.get("perms", []),
            "menus": perm_ctx.get("menus", []),
            "data_perms": perm_ctx.get("data_perms", {}),
        },
    }


async def refresh(db: AsyncSession, refresh_token: str) -> dict:
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise AppException("刷新令牌无效或已过期", ErrorCode.UNAUTHORIZED, 401)
    if redis.get(f"deny:{refresh_token[-16:]}"):
        raise AppException("刷新令牌已失效", ErrorCode.UNAUTHORIZED, 401)

    user_id = int(payload["sub"])
    result = await db.execute(
        select(SysUser).where(SysUser.id == user_id, SysUser.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None or user.status != 1:
        raise AppException("用户不存在或已禁用", ErrorCode.UNAUTHORIZED, 401)

    return {
        "access_token": create_access_token(user.id, {"username": user.username}),
        "token_type": "Bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


def logout(access_token: str, refresh_token: Optional[str] = None) -> None:
    """把令牌加入黑名单（TTL = 剩余有效期，这里取配置上限）。"""
    if access_token:
        redis.setex(f"deny:{access_token[-16:]}", settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, "1")
    if refresh_token:
        redis.setex(
            f"deny:{refresh_token[-16:]}", settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400, "1"
        )


async def change_password(
    db: AsyncSession, user_id: int, old_password: str, new_password: str
) -> None:
    if len(new_password) < 8 or not any(c.isdigit() for c in new_password) \
            or not any(c.isalpha() for c in new_password):
        raise AppException("新密码至少 8 位，且需同时包含字母和数字", ErrorCode.BAD_REQUEST)

    result = await db.execute(select(SysUser).where(SysUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise AppException("用户不存在", ErrorCode.NOT_FOUND, 404)
    if not verify_password(old_password, user.password_hash):
        raise AppException("原密码不正确", ErrorCode.BAD_REQUEST)

    user.password_hash = hash_password(new_password)
    user.pwd_must_change = False
    user.updated_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info("用户 %s 修改密码成功", user.username)
