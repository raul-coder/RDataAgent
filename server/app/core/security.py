"""认证与安全：口令散列、JWT 签发与校验。

口令格式兼容两种：
  pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>   —— 标准库实现，造数脚本默认格式
  $2b$...                                            —— bcrypt，安装了 passlib 时新口令优先使用
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt

from .config import settings

_PBKDF2_ITERATIONS = 120000


# ── 口令 ────────────────────────────────────────────────────────────
def _pbkdf2_hash(password: str, salt: bytes, iterations: int = _PBKDF2_ITERATIONS) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def _pbkdf2_verify(password: str, encoded: str) -> bool:
    try:
        _, iterations, salt_b64, hash_b64 = encoded.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001 - 格式不符一律视为校验失败
        return False


def hash_password(password: str) -> str:
    """优先使用 bcrypt；不可用时回退到 pbkdf2_sha256。"""
    try:
        from passlib.context import CryptContext  # type: ignore

        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return ctx.hash(password)
    except Exception:  # noqa: BLE001
        return _pbkdf2_hash(password, os.urandom(16))


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    if hashed.startswith("pbkdf2_sha256$"):
        return _pbkdf2_verify(password, hashed)
    if hashed.startswith("$2"):
        try:
            from passlib.context import CryptContext  # type: ignore

            ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
            return bool(ctx.verify(password, hashed))
        except Exception:  # noqa: BLE001
            return False
    return False


# ── JWT ─────────────────────────────────────────────────────────────
def create_access_token(
    subject: str,
    extra: Optional[dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": "access",
        "exp": expire,
        "iat": now,
        # jti 保证令牌唯一。缺了它，同一秒内多次登录会拿到**完全相同**的
        # 令牌：登出（进黑名单）后立刻重新登录，新令牌与刚拉黑的那个一样，
        # 结果一登录就被判无效。同时它也让我们能区分同一用户的不同会话。
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(subject), "type": "refresh", "exp": expire, "iat": now,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict[str, Any]]:
    """校验并解码 JWT，失败返回 None（不抛异常，便于中间件统一处理）。"""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
