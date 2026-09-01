"""角色与权限配置接口。"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, require_perm
from ...core.response import ok, paged
from ...db.session import get_db
from ...services import log_service, role_service

router = APIRouter(prefix="/roles", tags=["roles"])


class RoleCreateReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=1, max_length=64)
    description: str = ""


class RoleUpdateReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class MenuPermItem(BaseModel):
    id: int
    checked: bool = False
    ops: List[str] = Field(default_factory=list)


class DataPermItem(BaseModel):
    menu_id: int
    unit_codes: List[str] = Field(default_factory=list)


class SavePermReq(BaseModel):
    menus: List[MenuPermItem] = Field(default_factory=list)
    data_perms: Optional[List[DataPermItem]] = None


@router.get("", summary="角色列表")
async def list_roles(
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:role:view")),
):
    result = await role_service.query_roles(db, keyword=keyword, page=page, page_size=page_size)
    return paged(result["items"], result["total"], result["page"], result["page_size"])


@router.post("", summary="新增角色")
async def create_role(
    req: RoleCreateReq,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:role:add")),
):
    role = await role_service.create_role(
        db, name=req.name, code=req.code, description=req.description
    )
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"新增角色-{req.name}", method="POST /api/v1/roles", status="成功",
    )
    await db.commit()
    return ok({"id": role.id, "code": role.code})


@router.put("/{role_id}", summary="编辑角色")
async def update_role(
    role_id: int,
    req: RoleUpdateReq,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:role:edit")),
):
    role = await role_service.update_role(db, role_id, name=req.name, description=req.description)
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"修改角色-{role.name}", method="PUT /api/v1/roles/{id}", status="成功",
    )
    await db.commit()
    return ok({"id": role.id})


@router.delete("/{role_id}", summary="删除角色")
async def delete_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:role:del")),
):
    await role_service.delete_role(db, role_id)
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"删除角色-{role_id}", method="DELETE /api/v1/roles/{id}", status="成功",
    )
    await db.commit()
    return ok({"deleted": True})


@router.get("/{role_id}/permissions", summary="角色权限（菜单树 + 数据权限）")
async def get_permissions(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:perm:view")),
):
    return ok(await role_service.get_role_permissions(db, role_id))


@router.put("/{role_id}/permissions", summary="保存角色权限")
async def save_permissions(
    role_id: int,
    req: SavePermReq,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:perm:edit")),
):
    await role_service.save_role_permissions(
        db,
        role_id,
        menus=[m.model_dump() for m in req.menus],
        data_perms=None if req.data_perms is None else [d.model_dump() for d in req.data_perms],
    )
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"修改角色权限-{role_id}", method="PUT /api/v1/roles/{id}/permissions",
        status="成功",
    )
    await db.commit()
    return ok({"saved": True})
