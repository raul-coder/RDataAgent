"""角色 / 菜单 / 权限配置服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import AppException, ErrorCode
from ..core.logging import get_logger
from ..models import (
    SysMenu,
    SysRole,
    SysRoleDataPerm,
    SysRoleMenu,
    SysUser,
    SysUserRole,
)
from . import perm_service

logger = get_logger(__name__)

PERM_TYPES = ("view", "operate", "delete")


# ── 角色 ────────────────────────────────────────────────────────────
async def query_roles(
    db: AsyncSession, keyword: str = "", page: int = 1, page_size: int = 20
) -> dict:
    stmt = select(SysRole)
    if keyword:
        stmt = stmt.where(SysRole.name.ilike(f"%{keyword}%"))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(SysRole.id).offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    # 各角色用户数
    counts: Dict[int, int] = {}
    if rows:
        crows = (
            await db.execute(
                select(SysUserRole.role_id, func.count(SysUserRole.user_id))
                .where(SysUserRole.role_id.in_([r.id for r in rows]))
                .group_by(SysUserRole.role_id)
            )
        ).all()
        counts = {r[0]: r[1] for r in crows}

    return {
        "items": [
            {
                "id": r.id,
                "code": r.code,
                "name": r.name,
                "description": r.description or "",
                "is_builtin": bool(r.is_builtin),
                "user_count": counts.get(r.id, 0),
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
                "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else "",
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def create_role(
    db: AsyncSession, name: str, code: str, description: str = ""
) -> SysRole:
    if (
        await db.execute(select(func.count()).select_from(SysRole).where(SysRole.code == code))
    ).scalar_one():
        raise AppException(f"角色编码 {code} 已存在", ErrorCode.CONFLICT, 409)

    role = SysRole(
        code=code,
        name=name,
        description=description,
        is_builtin=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(role)
    await db.flush()
    return role


async def update_role(
    db: AsyncSession, role_id: int, name: Optional[str] = None, description: Optional[str] = None
) -> SysRole:
    res = await db.execute(select(SysRole).where(SysRole.id == role_id))
    role = res.scalar_one_or_none()
    if role is None:
        raise AppException("角色不存在", ErrorCode.NOT_FOUND, 404)
    if name is not None:
        role.name = name
    if description is not None:
        role.description = description
    role.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return role


async def delete_role(db: AsyncSession, role_id: int) -> None:
    res = await db.execute(select(SysRole).where(SysRole.id == role_id))
    role = res.scalar_one_or_none()
    if role is None:
        raise AppException("角色不存在", ErrorCode.NOT_FOUND, 404)
    if role.is_builtin:
        raise AppException("内置角色不可删除", ErrorCode.CONFLICT, 409)

    used = (
        await db.execute(
            select(func.count()).select_from(SysUserRole).where(SysUserRole.role_id == role_id)
        )
    ).scalar_one()
    if used:
        raise AppException(f"该角色下仍有 {used} 位用户，请先解除关联", ErrorCode.CONFLICT, 409)

    await db.execute(SysRoleMenu.__table__.delete().where(SysRoleMenu.role_id == role_id))
    await db.execute(
        SysRoleDataPerm.__table__.delete().where(SysRoleDataPerm.role_id == role_id)
    )
    await db.execute(SysRole.__table__.delete().where(SysRole.id == role_id))
    await db.flush()


# ── 菜单 ────────────────────────────────────────────────────────────
async def menu_tree(db: AsyncSession, role_id: Optional[int] = None) -> List[dict]:
    """菜单树；role_id 非空时附带该角色的勾选状态。"""
    rows = (await db.execute(select(SysMenu).order_by(SysMenu.sort_order, SysMenu.id))).scalars().all()

    checked: Dict[int, List[str]] = {}
    if role_id:
        rm = (
            await db.execute(select(SysRoleMenu).where(SysRoleMenu.role_id == role_id))
        ).scalars().all()
        checked = {r.menu_id: list(r.ops or []) for r in rm}

    def node(m: SysMenu) -> dict:
        return {
            "id": m.id,
            "parent_id": m.parent_id,
            "name": m.name,
            "path": m.path or "",
            "component": m.component or "",
            "icon": m.icon or "",
            "sort_order": m.sort_order,
            "type": m.type,
            "perm_code": m.perm_code or "",
            "visible": bool(m.visible),
            "ops": checked.get(m.id, []),
            "checked": m.id in checked,
            "children": [],
        }

    nodes = {m.id: node(m) for m in rows}
    roots: List[dict] = []
    for m in rows:
        n = nodes[m.id]
        if m.parent_id and m.parent_id in nodes:
            nodes[m.parent_id]["children"].append(n)
        else:
            roots.append(n)
    return roots


async def create_menu(db: AsyncSession, **kwargs) -> SysMenu:
    menu = SysMenu(
        parent_id=kwargs.get("parent_id", 0),
        name=kwargs["name"],
        path=kwargs.get("path", ""),
        component=kwargs.get("component", ""),
        icon=kwargs.get("icon", ""),
        sort_order=kwargs.get("sort_order", 0),
        type=kwargs.get("type", "C"),
        perm_code=kwargs.get("perm_code", ""),
        visible=kwargs.get("visible", True),
        created_at=datetime.now(timezone.utc),
    )
    db.add(menu)
    await db.flush()
    return menu


async def update_menu(db: AsyncSession, menu_id: int, **kwargs) -> SysMenu:
    res = await db.execute(select(SysMenu).where(SysMenu.id == menu_id))
    menu = res.scalar_one_or_none()
    if menu is None:
        raise AppException("菜单不存在", ErrorCode.NOT_FOUND, 404)
    for k in ("parent_id", "name", "path", "component", "icon", "sort_order", "type", "perm_code"):
        if k in kwargs and kwargs[k] is not None:
            setattr(menu, k, kwargs[k])
    if "visible" in kwargs and kwargs["visible"] is not None:
        menu.visible = bool(kwargs["visible"])
    await db.flush()
    return menu


async def delete_menu(db: AsyncSession, menu_id: int) -> None:
    has_child = (
        await db.execute(
            select(func.count()).select_from(SysMenu).where(SysMenu.parent_id == menu_id)
        )
    ).scalar_one()
    if has_child:
        raise AppException("存在子菜单，请先删除子项", ErrorCode.CONFLICT, 409)
    await db.execute(SysRoleMenu.__table__.delete().where(SysRoleMenu.menu_id == menu_id))
    await db.execute(SysRoleDataPerm.__table__.delete().where(SysRoleDataPerm.menu_id == menu_id))
    await db.execute(SysMenu.__table__.delete().where(SysMenu.id == menu_id))
    await db.flush()


# ── 权限配置 ────────────────────────────────────────────────────────
async def get_role_permissions(db: AsyncSession, role_id: int) -> dict:
    menus = await menu_tree(db, role_id=role_id)
    dp = (
        await db.execute(select(SysRoleDataPerm).where(SysRoleDataPerm.role_id == role_id))
    ).scalars().all()

    data_perms: List[dict] = []
    for row in dp:
        if row.perm_type != "view":
            continue
        data_perms.append(
            {"menu_id": row.menu_id, "perm_type": row.perm_type, "unit_codes": list(row.unit_codes or [])}
        )
    return {"role_id": role_id, "menus": menus, "data_perms": data_perms}


async def save_role_permissions(
    db: AsyncSession,
    role_id: int,
    menus: List[dict],
    data_perms: Optional[List[dict]] = None,
) -> None:
    """保存角色权限：菜单勾选 + 操作位 + 数据权限。"""
    res = await db.execute(select(SysRole).where(SysRole.id == role_id))
    if res.scalar_one_or_none() is None:
        raise AppException("角色不存在", ErrorCode.NOT_FOUND, 404)

    # 菜单权限
    await db.execute(SysRoleMenu.__table__.delete().where(SysRoleMenu.role_id == role_id))
    for item in menus or []:
        if not item.get("checked"):
            continue
        db.add(
            SysRoleMenu(
                role_id=role_id,
                menu_id=int(item["id"]),
                ops=list(item.get("ops") or []),
            )
        )
    await db.flush()

    # 数据权限
    if data_perms is not None:
        await db.execute(
            SysRoleDataPerm.__table__.delete().where(SysRoleDataPerm.role_id == role_id)
        )
        for item in data_perms or []:
            menu_id = int(item["menu_id"])
            unit_codes = list(item.get("unit_codes") or [])
            # view/operate/delete 三份记录保持一致，便于后续扩展
            for pt in PERM_TYPES:
                db.add(
                    SysRoleDataPerm(
                        role_id=role_id,
                        menu_id=menu_id,
                        perm_type=pt,
                        unit_codes=unit_codes if pt == "view" else unit_codes,
                    )
                )
        await db.flush()

    await perm_service.invalidate_role(db, role_id)
    logger.info("角色 %s 权限已更新", role_id)
