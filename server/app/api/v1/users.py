"""用户管理接口。"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, get_current_user, require_perm
from ...core.response import ok, paged
from ...db.session import get_db
from ...services import log_service, user_service
from ...services.perm_service import invalidate

router = APIRouter(prefix="/users", tags=["users"])


class UserCreateReq(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field("123456", min_length=6, max_length=128)
    nickname: str = ""
    phone: str = ""
    email: str = ""
    valid_until: Optional[str] = None
    role_ids: List[int] = Field(default_factory=list)


class UserUpdateReq(BaseModel):
    nickname: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    valid_until: Optional[str] = None
    role_ids: Optional[List[int]] = None


class BatchResetReq(BaseModel):
    user_ids: List[int]
    password: str = "123456"


@router.get("", summary="用户列表")
async def list_users(
    keyword: str = Query("", description="用户名/昵称/手机号"),
    role_id: Optional[int] = Query(None),
    status: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:user:view")),
):
    result = await user_service.query_users(
        db, keyword=keyword, role_id=role_id, status=status, page=page, page_size=page_size
    )
    return paged(result["items"], result["total"], result["page"], result["page_size"])


@router.get("/{user_id}", summary="用户详情")
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:user:view")),
):
    user = await user_service.get_user(db, user_id)
    if user is None:
        return ok(None)
    roles = await user_service._roles_of(db, [user_id])
    return ok(user_service._vo(user, roles.get(user_id, [])))


@router.post("", summary="新增用户")
async def create_user(
    req: UserCreateReq,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:user:add")),
):
    user = await user_service.create_user(
        db,
        username=req.username,
        role_ids=req.role_ids,
        nickname=req.nickname,
        phone=req.phone,
        email=req.email,
        valid_until=req.valid_until,
        password=req.password,
    )
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"新增用户-{req.username}", method="POST /api/v1/users", status="成功",
    )
    await db.commit()
    return ok({"id": user.id, "username": user.username})


@router.put("/{user_id}", summary="编辑用户")
async def update_user(
    user_id: int,
    req: UserUpdateReq,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:user:edit")),
):
    user = await user_service.update_user(
        db,
        user_id,
        nickname=req.nickname,
        phone=req.phone,
        email=req.email,
        valid_until=req.valid_until,
        role_ids=req.role_ids,
    )
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"修改用户-{user.username}", method="PUT /api/v1/users/{id}", status="成功",
    )
    await db.commit()
    return ok({"id": user.id})


@router.delete("/{user_id}", summary="删除用户")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:user:del")),
):
    await user_service.delete_user(db, user_id)
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"删除用户-{user_id}", method="DELETE /api/v1/users/{id}", status="成功",
    )
    await db.commit()
    return ok({"deleted": True})


@router.put("/{user_id}/status", summary="启用/禁用")
async def toggle_status(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:user:edit")),
):
    status = await user_service.toggle_status(db, user_id)
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"修改用户状态-{user_id}->{status}", method="PUT /api/v1/users/{id}/status",
        status="成功",
    )
    await db.commit()
    return ok({"id": user_id, "status": status})


@router.post("/batch-reset-password", summary="批量重置密码")
async def batch_reset_password(
    req: BatchResetReq,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:user:edit")),
):
    n = await user_service.reset_password(db, req.user_ids, req.password)
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"批量重置密码-{n} 个用户", method="POST /api/v1/users/batch-reset-password",
        status="成功",
    )
    await db.commit()
    return ok({"reset": n})
