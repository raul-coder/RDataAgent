"""应用配置接口（T4-1）：6 张配置卡片的读写。

读接口只要求登录——问数页需要据此决定是否展示开场白、追问建议、语音按钮；
写接口需要 ``sys:config:edit``，且每次变更都会落一条操作日志。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, get_current_user, require_perm
from ...core.response import ok
from ...db.session import get_db
from ...services import config_service, log_service

router = APIRouter(prefix="/app-config", tags=["app-config"])


class AppConfigBatchIn(BaseModel):
    configs: Dict[str, Any]


@router.get("", summary="应用配置全量（含未落库默认值）")
async def get_config(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    return ok(await config_service.get_all(db))


@router.get("/schema", summary="配置卡片结构（前端据此渲染 6 张卡片）")
async def get_schema(_: CurrentUser = Depends(get_current_user)):
    return ok(config_service.CARDS)


@router.put("", summary="批量保存应用配置")
async def save_config(
    req: AppConfigBatchIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:config:edit")),
):
    unknown = [k for k in req.configs if k not in config_service.DEFAULTS]
    if unknown:
        # 静默忽略未知键，避免前端传多余字段导致整批失败
        for k in unknown:
            req.configs.pop(k)

    await config_service.set_many(db, req.configs, user_id=current.id)
    await log_service.record(
        db,
        user_id=current.id,
        username=current.username,
        action=f"修改应用配置-{'/'.join(req.configs.keys()) or '空'}",
        method="PUT /api/v1/app-config",
        ip=request.client.host if request.client else "",
        status="成功",
    )
    await db.commit()
    return ok({"saved": sorted(req.configs.keys())})


@router.post("/reset", summary="恢复默认配置")
async def reset_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:config:edit")),
):
    await config_service.set_many(db, dict(config_service.DEFAULTS), user_id=current.id)
    await log_service.record(
        db,
        user_id=current.id,
        username=current.username,
        action="恢复应用配置默认值",
        method="POST /api/v1/app-config/reset",
        ip=request.client.host if request.client else "",
        status="成功",
    )
    await db.commit()
    return ok({"reset": True})
