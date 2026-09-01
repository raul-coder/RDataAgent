"""RBAC 内核测试（依赖本机 PostgreSQL 中的种子数据）。

运行：pytest tests/test_permissions.py
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.perm_service import load_user_permissions, ops_to_perms

DSN = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://jingguan:jingguan@localhost:5432/jingguan"
)


@pytest.fixture
async def db() -> AsyncSession:
    engine = create_async_engine(DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # 确认种子数据已装载
        n = (await session.execute(text("SELECT COUNT(*) FROM sys_user"))).scalar_one()
        if n == 0:
            pytest.skip("数据库未装载种子数据，请先执行 make db-init")
        yield session
    await engine.dispose()


async def _uid(session: AsyncSession, username: str) -> int:
    from app.models import SysUser

    res = await session.execute(select(SysUser.id).where(SysUser.username == username))
    return res.scalar_one()


async def test_ops_to_perms_derivation():
    """菜单权限码 + 操作位 → 权限码集合。"""
    perms = ops_to_perms("sys:user:view", ["edit", "export"])
    assert "sys:user:view" in perms
    assert "sys:user:edit" in perms
    assert "sys:user:export" in perms

    # 无 :view 后缀的菜单（如 ai:qa）
    perms2 = ops_to_perms("ai:qa", ["export"])
    assert "ai:qa" in perms2
    assert "ai:qa:export" in perms2

    assert ops_to_perms(None, ["view"]) == set()


async def test_superadmin_permissions(db: AsyncSession):
    """超级管理员：全部菜单与权限。"""
    uid = await _uid(db, "admin")
    ctx = await load_user_permissions(db, uid, use_cache=False)

    assert "SUPER_ADMIN" in ctx["role_codes"]
    assert len(ctx["menus"]) == 20
    assert "sys:user:view" in ctx["perms"]
    assert "sys:user:edit" in ctx["perms"]
    # 内置角色的空名单 → 数据权限不限制
    assert all(v is None for v in ctx["data_perms"].values())


async def test_normal_user_permissions(db: AsyncSession):
    """普通用户：菜单受限，且数据权限只覆盖 2 个经营单元（UC-5）。"""
    uid = await _uid(db, "zhangsan")
    ctx = await load_user_permissions(db, uid, use_cache=False)

    names = [m["name"] for m in ctx["menus"]]
    assert "用户管理" not in names
    assert "角色管理" not in names
    assert "智能问数" in names
    assert "sys:user:view" not in ctx["perms"]

    # 数据权限：智能问数(menu 1) 仅可见上海、浙江
    assert ctx["data_perms"].get("1") == ["SH", "ZJ"]


async def test_auditor_permissions(db: AsyncSession):
    """审计员：仅日志与反馈相关，且无编辑类权限。"""
    uid = await _uid(db, "xushi")
    ctx = await load_user_permissions(db, uid, use_cache=False)

    names = [m["name"] for m in ctx["menus"]]
    assert "操作日志" in names
    assert "sys:log:export" in ctx["perms"]
    assert not any(p.startswith("sys:user:") for p in ctx["perms"])
