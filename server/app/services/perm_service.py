"""RBAC 内核：权限加载、缓存与失效。

权限模型：
    用户 ──< 用户角色 >── 角色 ──< 角色菜单(ops) >── 菜单
                              └──< 角色数据权限(菜单 × view/operate/delete × unit_codes) >

权限码推导：
    菜单 perm_code = 'sys:user:view'，角色拥有 ops=['edit','export']
      → 'sys:user:view'、'sys:user:edit'、'sys:user:export'
    menus 无 ':view' 后缀时（如 'ai:qa'）→ 'ai:qa:edit'
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import redis
from ..core.logging import get_logger
from ..models import SysMenu, SysRole, SysRoleDataPerm, SysRoleMenu, SysUser, SysUserRole

logger = get_logger(__name__)

CACHE_TTL = 1800  # 30 分钟


def _cache_key(user_id: int) -> str:
    return f"perms:{user_id}"


def ops_to_perms(perm_code: Optional[str], ops: Sequence[str]) -> Set[str]:
    """菜单权限码 + 操作位 → 权限码集合。"""
    if not perm_code:
        return set()
    perms = {perm_code}
    # 'sys:user:view' → 前缀 'sys:user:'（去掉 'view' 4 个字符，保留末尾冒号）
    prefix = perm_code[:-4] if perm_code.endswith(":view") else perm_code + ":"
    for op in ops or []:
        perms.add(prefix + op)
    return perms


async def load_user_permissions(
    db: AsyncSession, user_id: int, use_cache: bool = True
) -> Dict:
    """加载用户的菜单树、权限码与数据权限。"""
    if use_cache:
        cached = redis.get(_cache_key(user_id))
        if cached:
            try:
                return json.loads(cached)
            except Exception:  # noqa: BLE001
                logger.warning("权限缓存解析失败，重新加载 user_id=%s", user_id)

    # 角色
    role_ids = list(
        (
            await db.execute(select(SysUserRole.role_id).where(SysUserRole.user_id == user_id))
        ).scalars()
    )
    role_codes = (
        list(
            (
                await db.execute(select(SysRole.code).where(SysRole.id.in_(role_ids)))
            ).scalars()
        )
        if role_ids
        else []
    )

    perms: Set[str] = set()
    menus: List[SysMenu] = []
    data_perms: Dict[int, Optional[List[str]]] = {}

    if role_ids:
        # 角色 → 菜单（含 ops）
        rm_rows = (
            await db.execute(
                select(SysMenu, SysRoleMenu.ops).join(
                    SysRoleMenu, SysRoleMenu.menu_id == SysMenu.id
                ).where(SysRoleMenu.role_id.in_(role_ids))
            )
        ).all()
        menu_map: Dict[int, SysMenu] = {}
        ops_map: Dict[int, Set[str]] = {}
        for menu, ops in rm_rows:
            menu_map[menu.id] = menu
            ops_map.setdefault(menu.id, set()).update(ops or [])
        menus = sorted(menu_map.values(), key=lambda m: (m.sort_order, m.id))
        for mid, ops in ops_map.items():
            perms |= ops_to_perms(menu_map[mid].perm_code, sorted(ops))

        # 数据权限：任一角色为空名单 → 该菜单不限制
        dp_rows = (
            await db.execute(
                select(SysRoleDataPerm).where(
                    SysRoleDataPerm.role_id.in_(role_ids),
                    SysRoleDataPerm.perm_type == "view",
                )
            )
        ).scalars().all()
        for row in dp_rows:
            codes = list(row.unit_codes or [])
            if not codes:
                data_perms[row.menu_id] = None  # 不限制
            elif row.menu_id in data_perms and data_perms[row.menu_id] is not None:
                merged = set(data_perms[row.menu_id]) | set(codes)
                data_perms[row.menu_id] = sorted(merged)
            else:
                data_perms[row.menu_id] = sorted(set(codes))

    result = {
        "user_id": user_id,
        "role_ids": role_ids,
        "role_codes": role_codes,
        "perms": sorted(perms),
        "menus": [
            {
                "id": m.id,
                "parent_id": m.parent_id,
                "name": m.name,
                "path": m.path,
                "component": m.component,
                "icon": m.icon,
                "sort_order": m.sort_order,
                "type": m.type,
                "perm_code": m.perm_code,
                "visible": m.visible,
            }
            for m in menus
        ],
        # JSON 的 key 只能是字符串
        "data_perms": {str(k): v for k, v in data_perms.items()},
    }

    if use_cache:
        redis.setex(_cache_key(user_id), CACHE_TTL, json.dumps(result, ensure_ascii=False))
    return result


def invalidate(user_id: Optional[int] = None) -> int:
    """失效权限缓存。user_id 为空时清空全部用户缓存。"""
    if user_id is not None:
        redis.delete(_cache_key(user_id))
        return 1
    return redis.delete_prefix("perms:")


async def invalidate_role(db: AsyncSession, role_id: int) -> int:
    """角色权限变更 → 失效该角色下全部用户的权限缓存。"""
    user_ids = list(
        (
            await db.execute(select(SysUserRole.user_id).where(SysUserRole.role_id == role_id))
        ).scalars()
    )
    for uid in user_ids:
        redis.delete(_cache_key(uid))
    logger.info("角色 %s 权限变更，已失效 %d 位用户的权限缓存", role_id, len(user_ids))
    return len(user_ids)


def visible_units(perms: Dict, menu_id: int) -> Optional[List[str]]:
    """取某菜单的经营单元可见范围；None 表示不限制。"""
    return perms.get("data_perms", {}).get(str(menu_id))
