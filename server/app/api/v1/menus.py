"""菜单管理 + 权限选项 + 操作日志接口。"""

from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, require_perm
from ...core.exceptions import AppException, ErrorCode
from ...core.response import ok, paged
from ...db.session import get_db
from ...models import SysMenu
from ...services import log_service, role_service
from ...services.perm_service import ops_to_perms

router = APIRouter(prefix="/menus", tags=["menus"])


class MenuReq(BaseModel):
    parent_id: int = 0
    name: str
    path: str = ""
    component: str = ""
    icon: str = ""
    sort_order: int = 0
    type: str = "C"
    perm_code: str = ""
    visible: bool = True


@router.get("", summary="菜单树")
async def list_menus(
    role_id: Optional[int] = Query(None, description="传入则附带该角色勾选状态"),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:menu:view")),
):
    return ok(await role_service.menu_tree(db, role_id=role_id))


@router.post("", summary="新增菜单")
async def create_menu(
    req: MenuReq,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:menu:add")),
):
    menu = await role_service.create_menu(db, **req.model_dump())
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"新增菜单-{req.name}", method="POST /api/v1/menus", status="成功",
    )
    await db.commit()
    return ok({"id": menu.id})


@router.put("/{menu_id}", summary="编辑菜单")
async def update_menu(
    menu_id: int,
    req: MenuReq,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:menu:edit")),
):
    menu = await role_service.update_menu(db, menu_id, **req.model_dump())
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"修改菜单-{menu.name}", method="PUT /api/v1/menus/{id}", status="成功",
    )
    await db.commit()
    return ok({"id": menu.id})


@router.delete("/{menu_id}", summary="删除菜单")
async def delete_menu(
    menu_id: int,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:menu:del")),
):
    await role_service.delete_menu(db, menu_id)
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"删除菜单-{menu_id}", method="DELETE /api/v1/menus/{id}", status="成功",
    )
    await db.commit()
    return ok({"deleted": True})


# ── 权限选项（前端权限配置页 / 数据权限下拉用）────────────────────
options_router = APIRouter(prefix="/permissions", tags=["permissions"])


@options_router.get("/options", summary="操作权限位与经营单元选项")
async def permission_options(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:perm:view")),
):
    from ...services.role_service import PERM_TYPES  # 局部导入避免循环

    units = await db.execute(
        select(SysMenu).where(SysMenu.type == "C")  # 占位：真实单元取自 bi.dim_unit
    )
    return ok(
        {
            "operations": [
                ("view", "查看"), ("add", "新增"), ("edit", "编辑"), ("del", "删除"),
                ("import", "导入"), ("export", "导出"), ("refresh", "刷新"),
                ("batch", "批量操作"), ("filter", "筛选"), ("query", "查询"),
            ],
            "perm_types": list(PERM_TYPES),
            "menus": [
                {"id": m.id, "name": m.name, "perm_code": m.perm_code}
                for m in (await db.execute(select(SysMenu).order_by(SysMenu.id))).scalars().all()
            ],
        }
    )


@options_router.get("/preview", summary="预览某菜单权限码推导结果")
async def preview_perms(perm_code: str, ops: str = ""):
    return ok(sorted(ops_to_perms(perm_code, [o for o in ops.split(",") if o])))


# ── 经营单元（数据权限选项），只读业务库 ──────────────────────────
@options_router.get("/units", summary="经营单元列表（数据权限用）")
async def list_units(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:perm:view")),
):
    from sqlalchemy import text

    rows = (
        await db.execute(
            text("SELECT unit_code, unit_name, region FROM bi.dim_unit ORDER BY unit_code")
        )
    ).all()
    return ok([{"code": r[0], "name": r[1], "region": r[2]} for r in rows])


# ── 操作日志 ───────────────────────────────────────────────────────
logs_router = APIRouter(prefix="/logs", tags=["logs"])


def _log_filters(
    keyword: str = Query(""),
    username: str = Query(""),
    log_type: str = Query("", description="login / oper"),
    status: str = Query(""),
    start_time: str = Query("", description="起始时间 YYYY-MM-DD[ HH:MM:SS]"),
    end_time: str = Query("", description="结束时间 YYYY-MM-DD[ HH:MM:SS]"),
) -> dict:
    """查询与导出共用同一套筛选参数。"""
    return {
        "keyword": keyword,
        "username": username,
        "log_type": log_type,
        "status": status,
        "start_time": start_time or None,
        "end_time": end_time or None,
    }


@logs_router.get("/operation", summary="操作日志")
async def operation_logs(
    filters: dict = Depends(_log_filters),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:log:view")),
):
    from ...services import log_service as ls

    try:
        result = await ls.query(
            db, **filters, page=page, page_size=page_size
        )
    except ValueError as exc:
        raise AppException(f"时间格式不正确：{exc}", ErrorCode.BAD_REQUEST) from exc
    return paged(result["items"], result["total"], result["page"], result["page_size"])


@logs_router.get("/operation/export", summary="导出操作日志（CSV）")
async def export_operation_logs(
    filters: dict = Depends(_log_filters),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:log:export")),
):
    """按当前筛选条件导出 CSV（utf-8-sig 带 BOM，Excel 打开不乱码）。"""
    from datetime import datetime, timezone

    from ...services import log_service as ls

    try:
        rows = await ls.export_rows(db, **filters)
    except ValueError as exc:
        raise AppException(f"时间格式不正确：{exc}", ErrorCode.BAD_REQUEST) from exc

    columns = [
        ("created_at", "时间"), ("username", "用户"), ("log_type", "类型"),
        ("action", "动作"), ("method", "方法"), ("ip", "IP"), ("status", "状态"),
    ]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in columns])
    for r in rows:
        writer.writerow([r.get(key, "") for key, _ in columns])

    # 导出本身也要留痕（合规要求：日志操作可被审计）
    try:
        await ls.record(
            db, user_id=current.id, username=current.username,
            action=f"导出操作日志-{len(rows)}条",
            method="GET /api/v1/logs/operation/export",
            ip=request.client.host if request else "",
            status="成功",
        )
        await db.commit()
    except Exception:  # noqa: BLE001 - 审计落盘失败不应阻断导出
        pass

    filename = datetime.now(timezone.utc).strftime("operation-log-%Y%m%d%H%M%S.csv")
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
