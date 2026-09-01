"""限流：基于 Redis 的固定窗口计数（Redis 不可用时自动降级放行）。

用法：
    check_rate_limit("login:zhangsan", limit=5, window=600)
"""

from __future__ import annotations

from fastapi import Request

from . import redis
from .config import settings
from .exceptions import AppException, ErrorCode
from .logging import get_logger

logger = get_logger(__name__)

# key 前缀 -> (次数上限, 窗口秒数)
POLICIES = {
    "login": (10, 600),      # 同一用户名 10 次 / 10 分钟（防暴力破解）
    "ip": (60, 600),         # 同一 IP 60 次 / 10 分钟
    "qa": (10, 60),          # 问数 10 次 / 分钟
}

# 非生产环境放宽倍数：本地演示与自动化验证会在短时间内连续提问
# （如 UC-3 / UC-4 脚本一轮就登录 2~3 次），按生产额度会被 429 打断。
# 生产环境（ENV=prod）保持原额度不变。
_RELAX_FACTOR = 6


def _policy(name: str) -> tuple[int, int]:
    limit, window = POLICIES[name]
    if settings.ENV == "prod":
        return limit, window
    return limit * _RELAX_FACTOR, window


def _parse(value: str) -> tuple[int, int]:
    """'10/minute' -> (10, 60)"""
    try:
        n, unit = value.split("/")
        return int(n), {"second": 1, "minute": 60, "hour": 3600}.get(unit.strip(), 60)
    except Exception:  # noqa: BLE001
        return 10, 60


def check_rate_limit(key: str, limit: int, window: int) -> None:
    if redis.is_degraded():
        return  # 降级放行，避免因缓存故障导致整站不可用
    used = redis.incr(f"rl:{key}", window)
    if used > limit:
        raise AppException(
            f"操作过于频繁，请 {window} 秒后再试",
            ErrorCode.RATE_LIMITED,
            429,
        )


def check_login(username: str, request: Request) -> None:
    limit, window = _policy("login")
    check_rate_limit(f"login:{username}", limit, window)
    ip = request.client.host if request.client else "unknown"
    ilimit, iwindow = _policy("ip")
    check_rate_limit(f"ip:{ip}", ilimit, iwindow)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
