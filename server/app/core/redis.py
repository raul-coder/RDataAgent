"""Redis 客户端（带内存兜底）。

Redis 不可用时自动降级为进程内字典，保证功能不中断（见技术方案 §11.4）。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from .logging import get_logger

logger = get_logger(__name__)

_client = None
_degraded = False
_memory: Dict[str, Tuple[str, float]] = {}  # key -> (value, expire_at)


def _get_client():
    global _client, _degraded
    if _degraded:
        return None
    if _client is None:
        try:
            import redis  # type: ignore

            from .config import settings

            _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            _client.ping()
            logger.info("Redis 已连接")
        except Exception as exc:  # noqa: BLE001
            _degraded = True
            _client = None
            logger.warning("Redis 不可用，降级为进程内存储：%s", exc)
    return _client


def is_degraded() -> bool:
    _get_client()
    return _degraded


def setex(key: str, ttl: int, value: str) -> None:
    c = _get_client()
    if c is not None:
        try:
            c.setex(key, ttl, value)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 写入失败，降级：%s", exc)
    _memory[key] = (value, time.time() + ttl)


def get(key: str) -> Optional[str]:
    c = _get_client()
    if c is not None:
        try:
            return c.get(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 读取失败，降级：%s", exc)
    item = _memory.get(key)
    if not item:
        return None
    value, expire_at = item
    if expire_at < time.time():
        _memory.pop(key, None)
        return None
    return value


def delete(key: str) -> None:
    c = _get_client()
    if c is not None:
        try:
            c.delete(key)
        except Exception:  # noqa: BLE001
            pass
    _memory.pop(key, None)


def delete_prefix(prefix: str) -> int:
    """按前缀批量删除（用于权限缓存失效）。"""
    c = _get_client()
    n = 0
    if c is not None:
        try:
            keys: List[str] = list(c.scan_iter(match=f"{prefix}*", count=1000))
            if keys:
                n = c.delete(*keys)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 批量删除失败，降级：%s", exc)
    for k in [k for k in _memory if k.startswith(prefix)]:
        _memory.pop(k, None)
        n += 1
    return n


def incr(key: str, ttl: int) -> int:
    """自增计数，首次设置时带上过期时间。"""
    c = _get_client()
    if c is not None:
        try:
            pipe = c.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl)
            result = pipe.execute()
            return int(result[0])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis incr 失败，降级：%s", exc)
    item = _memory.get(key)
    value = 1 if not item else int(item[0]) + 1
    _memory[key] = (str(value), time.time() + ttl)
    return value
