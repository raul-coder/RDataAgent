"""模型配置服务：多模型托管、密钥加密、连接测试、默认模型选择。

设计要点：
  1. API Key 只在落库时加密、取出时解密，**绝不出库**（列表一律返回脱敏串）。
  2. 模型名前缀推断与 ``LitellmProvider`` 共用 ``resolve_model_name``，
     避免「测试连接通过、真跑却报错」这类两端不一致。
  3. 默认模型按场景（scene）隔离：默认场景为智能问数 ``chat_qa``。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.crypto import decrypt_secret, encrypt_secret, mask_secret
from ..core.exceptions import NotFoundError
from ..core.logging import get_logger
from ..llm.litellm_provider import resolve_model_name
from ..models import SysModel
from . import qa_cache

logger = get_logger(__name__)

DEFAULT_SCENE = "chat_qa"


def _vo(row: SysModel, with_key: bool = False) -> Dict[str, Any]:
    plain = decrypt_secret(row.api_key_enc or "")
    data = {
        "id": row.id,
        "name": row.name,
        "provider": row.provider,
        "base_url": row.base_url,
        "model_name": row.model_name,
        "scene": row.scene,
        "is_default": row.is_default,
        "enabled": row.enabled,
        "params": row.params or {},
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        # 出库只给脱敏串；前端回传空串表示"不修改"
        "api_key_masked": mask_secret(plain),
        "has_key": bool(plain),
    }
    if with_key:
        data["api_key"] = plain
    return data


async def list_models(
    db: AsyncSession, scene: str = DEFAULT_SCENE, only_enabled: bool = False
) -> List[Dict[str, Any]]:
    """默认模型排在最前，其余按 id。"""
    stmt = select(SysModel).where(SysModel.scene == scene)
    if only_enabled:
        stmt = stmt.where(SysModel.enabled.is_(True))
    rows = (await db.execute(stmt.order_by(SysModel.is_default.desc(), SysModel.id))).scalars().all()
    return [_vo(r) for r in rows]


async def get_model(db: AsyncSession, model_id: int) -> SysModel:
    row = (
        await db.execute(select(SysModel).where(SysModel.id == model_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"模型 {model_id} 不存在")
    return row


async def create_model(db: AsyncSession, payload: Dict[str, Any]) -> int:
    row = SysModel(
        name=payload["name"],
        provider=payload.get("provider") or "openai",
        base_url=payload["base_url"],
        model_name=payload["model_name"],
        api_key_enc=encrypt_secret(payload.get("api_key") or ""),
        scene=payload.get("scene") or DEFAULT_SCENE,
        is_default=bool(payload.get("is_default")),
        enabled=bool(payload.get("enabled", True)),
        params=payload.get("params") or {},
    )
    db.add(row)
    await db.flush()
    if row.is_default:
        await _clear_other_defaults(db, row.id, row.scene)
        await db.flush()
    return row.id


async def update_model(db: AsyncSession, model_id: int, payload: Dict[str, Any]) -> None:
    row = await get_model(db, model_id)
    # 模型一变，既有问数缓存的结论就"过时"了（不同模型的表述与口径可能不同）
    qa_cache.clear()
    for field in ("name", "provider", "base_url", "model_name", "scene"):
        if field in payload and payload[field] is not None:
            setattr(row, field, payload[field])
    if "enabled" in payload and payload["enabled"] is not None:
        row.enabled = bool(payload["enabled"])
    if "params" in payload and payload["params"] is not None:
        row.params = payload["params"]
    # 空串视为"不修改密钥"，避免前端回显脱敏值把真密钥覆盖掉
    if payload.get("api_key"):
        row.api_key_enc = encrypt_secret(payload["api_key"])
    if "is_default" in payload and payload["is_default"]:
        row.is_default = True
        await _clear_other_defaults(db, row.id, row.scene)
    await db.flush()


async def delete_model(db: AsyncSession, model_id: int) -> None:
    row = await get_model(db, model_id)
    await db.delete(row)
    await db.flush()
    qa_cache.clear()


async def set_default(db: AsyncSession, model_id: int) -> None:
    row = await get_model(db, model_id)
    qa_cache.clear()
    if not row.enabled:
        row.enabled = True
    row.is_default = True
    await _clear_other_defaults(db, row.id, row.scene)
    await db.flush()


async def _clear_other_defaults(db: AsyncSession, keep_id: int, scene: str) -> None:
    others = (
        await db.execute(
            select(SysModel).where(
                SysModel.scene == scene, SysModel.id != keep_id, SysModel.is_default.is_(True)
            )
        )
    ).scalars().all()
    for o in others:
        o.is_default = False


async def active_models(db: AsyncSession, scene: str = DEFAULT_SCENE) -> List[Dict[str, Any]]:
    """供 Agent 链路使用：启用的模型，默认优先，含解密后的密钥（仅内存）。"""
    rows = (
        await db.execute(
            select(SysModel)
            .where(SysModel.scene == scene, SysModel.enabled.is_(True))
            .order_by(SysModel.is_default.desc(), SysModel.id)
        )
    ).scalars().all()
    return [_vo(r, with_key=True) for r in rows]


async def test_connection(
    base_url: str,
    model_name: str,
    api_key: str = "",
    provider: str = "openai",
    timeout: int = 60,
) -> Dict[str, Any]:
    """真实发一次请求验证连通性。

    只给 64 个 token 是刻意的：既要确认"能返回内容"，
    又不能因为用户填错参数而烧掉大量 token。
    """
    started = time.perf_counter()
    resolved = resolve_model_name(base_url, model_name)
    try:
        import litellm  # type: ignore

        resp = await litellm.acompletion(
            model=resolved,
            api_key=api_key or None,
            api_base=base_url or None,
            messages=[{"role": "user", "content": "回复 OK 两个字"}],
            max_tokens=64,
            temperature=0,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        cost = int((time.perf_counter() - started) * 1000)
        logger.warning("模型连通性测试失败 %s：%s", resolved, exc)
        return {"ok": False, "cost_ms": cost, "message": f"{type(exc).__name__}: {str(exc)[:180]}"}

    cost = int((time.perf_counter() - started) * 1000)
    content = (resp.choices[0].message.content or "").strip()
    return {
        "ok": True,
        "cost_ms": cost,
        "message": f"连接成功（{cost}ms）",
        "reply": content[:50],
        "model": resolved,
    }
