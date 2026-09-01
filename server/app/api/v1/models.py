"""模型配置接口（T4-2）：CRUD / 测试连接 / 设为默认。

安全约定：
  * API Key 只在请求体中**写入**，响应中永远只返回脱敏串（``api_key_masked``）。
  * 测试连接接受未保存的参数，因此独立成 ``/models/test``，不落库。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, require_perm
from ...core.response import ok
from ...db.session import get_db
from ...services import log_service, model_service

router = APIRouter(prefix="/models", tags=["models"])


class ModelIn(BaseModel):
    name: str
    provider: str = "openai"
    base_url: str
    model_name: str
    api_key: str = ""
    scene: str = "chat_qa"
    is_default: bool = False
    enabled: bool = True
    params: dict = {}


class TestConnIn(BaseModel):
    base_url: str
    model_name: str
    api_key: str = ""
    provider: str = "openai"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("", summary="模型列表（默认优先）")
async def list_models(
    scene: str = Query("chat_qa"),
    only_enabled: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:model:view")),
):
    return ok(await model_service.list_models(db, scene=scene, only_enabled=only_enabled))


@router.post("", summary="新增模型")
async def create_model(
    req: ModelIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:model:add")),
):
    mid = await model_service.create_model(db, req.model_dump())
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"新增模型-{req.name}", method="POST /api/v1/models",
        ip=_client_ip(request), status="成功",
    )
    await db.commit()
    return ok({"id": mid})


@router.put("/{model_id}", summary="编辑模型")
async def update_model(
    model_id: int,
    req: ModelIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:model:edit")),
):
    await model_service.update_model(db, model_id, req.model_dump())
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"修改模型-{req.name}", method="PUT /api/v1/models/{id}",
        ip=_client_ip(request), status="成功",
    )
    await db.commit()
    return ok({"id": model_id})


@router.delete("/{model_id}", summary="删除模型")
async def delete_model(
    model_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:model:del")),
):
    row = await model_service.get_model(db, model_id)
    await model_service.delete_model(db, model_id)
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"删除模型-{row.name}", method="DELETE /api/v1/models/{id}",
        ip=_client_ip(request), status="成功",
    )
    await db.commit()
    return ok({"deleted": True})


@router.post("/{model_id}/default", summary="设为默认模型")
async def set_default(
    model_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(require_perm("sys:model:edit")),
):
    row = await model_service.get_model(db, model_id)
    await model_service.set_default(db, model_id)
    await log_service.record(
        db, user_id=current.id, username=current.username,
        action=f"设为默认模型-{row.name}", method="POST /api/v1/models/{id}/default",
        ip=_client_ip(request), status="成功",
    )
    await db.commit()
    return ok({"id": model_id, "is_default": True})


@router.post("/test", summary="测试连接（不落库）")
async def test_connection(
    req: TestConnIn,
    _: CurrentUser = Depends(require_perm("sys:model:view")),
):
    return ok(
        await model_service.test_connection(
            base_url=req.base_url,
            model_name=req.model_name,
            api_key=req.api_key,
            provider=req.provider,
        )
    )


@router.post("/{model_id}/test", summary="测试已保存模型的连接")
async def test_saved_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sys:model:view")),
):
    from ...core.crypto import decrypt_secret

    row = await model_service.get_model(db, model_id)
    return ok(
        await model_service.test_connection(
            base_url=row.base_url,
            model_name=row.model_name,
            api_key=decrypt_secret(row.api_key_enc or ""),
            provider=row.provider,
        )
    )
