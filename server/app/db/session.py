"""数据库会话：主库（读写）+ 业务库只读连接（Agent 取数）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..core.config import settings

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None

_ro_engine: Optional[AsyncEngine] = None
_ro_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def init_engines() -> None:
    global _engine, _session_factory, _ro_engine, _ro_session_factory
    if _engine is None:
        _engine = create_async_engine(
            settings.DATABASE_URL, echo=settings.SQL_ECHO, pool_pre_ping=True
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    if _ro_engine is None:
        _ro_engine = create_async_engine(
            settings.bi_readonly_dsn, echo=False, pool_pre_ping=True
        )
        _ro_session_factory = async_sessionmaker(_ro_engine, expire_on_commit=False)


async def dispose_engines() -> None:
    global _engine, _ro_engine, _session_factory, _ro_session_factory
    if _engine is not None:
        await _engine.dispose()
    if _ro_engine is not None:
        await _ro_engine.dispose()
    _engine = _ro_engine = None
    _session_factory = _ro_session_factory = None


async def get_db() -> AsyncIterator[AsyncSession]:
    """主库读写会话（依赖注入用）。"""
    if _session_factory is None:
        init_engines()
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session


async def get_readonly_db() -> AsyncIterator[AsyncSession]:
    """只读会话：Agent 执行生成的 SQL 时使用。"""
    if _ro_session_factory is None:
        init_engines()
    assert _ro_session_factory is not None
    async with _ro_session_factory() as session:
        yield session


@asynccontextmanager
async def readonly_session() -> AsyncIterator[AsyncSession]:
    """在依赖注入之外使用只读会话（如后台任务）。"""
    if _ro_session_factory is None:
        init_engines()
    assert _ro_session_factory is not None
    async with _ro_session_factory() as session:
        yield session
