"""语义层接口：只读查询 + 指标 / 维度 / 口径规则 / Few-shot 样本的增删改查。

为什么要做管理页（O6 可持续运营）：
    语义层直接决定 Text2SQL 的准确率——指标口径、维度 JOIN、口径规则、
    Few-shot 样本都是「运营出来」的，不是配一次就结束。没有管理界面，
    每次调整都得改 SQL 脚本再重新装载数据。

安全约定：
    * 只读接口要求登录即可（问数页的数据源选择器要用）；
    * 写接口一律要求 ``sem:edit``（超级管理员放行），并落一条操作日志；
    * 删除采用软删除思路：只置 enabled=False，避免历史问数记录失去参照。

改动语义层的风险提示：Few-shot 的 SQL 可能因为表结构变更而失效，
因此提供 ``/fewshots/{id}/verify`` 单独验证一条样本。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import CurrentUser, get_current_user, require_perm
from ...core.exceptions import AppException, ErrorCode, NotFoundError
from ...core.logging import get_logger
from ...core.response import ok
from ...db.session import get_db, get_readonly_db
from ...models import (
    SemDataSource,
    SemDimension,
    SemFewshot,
    SemMetric,
    SemRule,
)
from ...services import log_service

logger = get_logger(__name__)

router = APIRouter(prefix="/data-sources", tags=["semantic"])
mgmt = APIRouter(prefix="/semantic", tags=["semantic"])
# 写接口按资源分别鉴权。权限码由「菜单 perm_code 前缀 + ops」推导：
#   sem:metric:view + ops=[edit] → sem:metric:edit（见 perm_service.ops_to_perms）
# 因此给角色勾选「编辑」操作位即可放开对应资源的写权限。


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


async def _audit(
    db: AsyncSession, user: CurrentUser, action: str, request: Request,
) -> None:
    await log_service.record(
        db, action=action, status="成功", user_id=user.id,
        username=user.username, ip=_ip(request),
    )


async def _get(db: AsyncSession, model, oid: int, what: str):
    row = (await db.execute(select(model).where(model.id == oid))).scalar_one_or_none()
    if not row:
        raise NotFoundError(f"{what} 不存在：{oid}")
    return row


# ════════════════════════════════════════════════════════════
# 只读：数据源 / 指标 / 维度 / 规则 / 样本
# ════════════════════════════════════════════════════════════
@router.get("", summary="数据源列表（分组）")
async def list_sources(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    rows = (
        await db.execute(
            select(SemDataSource)
            .where(SemDataSource.enabled.is_(True))
            .order_by(SemDataSource.sort_order, SemDataSource.id)
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": r.id,
                "group_name": r.group_name,
                "name": r.name,
                "object_name": r.object_name,
                "object_type": r.object_type,
                "description": r.description or "",
                "enabled": bool(r.enabled),
                "sort_order": r.sort_order,
            }
            for r in rows
        ]
    )


@router.get("/metrics", summary="指标列表")
async def list_metrics(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    rows = (await db.execute(select(SemMetric).order_by(SemMetric.id))).scalars().all()
    return ok(
        [
            {
                "id": r.id,
                "code": r.code,
                "name": r.name,
                "aliases": list(r.aliases or []),
                "expr_sql": r.expr_sql,
                "unit": r.unit,
                "caliber": r.caliber or "",
                "source_id": r.source_id,
                "enabled": bool(r.enabled),
            }
            for r in rows
        ]
    )


@router.get("/dimensions", summary="维度列表")
async def list_dimensions(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    rows = (await db.execute(select(SemDimension).order_by(SemDimension.id))).scalars().all()
    return ok(
        [
            {
                "id": r.id,
                "code": r.code,
                "name": r.name,
                "aliases": list(r.aliases or []),
                "expr_sql": r.expr_sql,
                "join_sql": r.join_sql or "",
                "dim_type": r.dim_type,
                "source_id": r.source_id,
                "enabled": bool(r.enabled),
            }
            for r in rows
        ]
    )


@mgmt.get("/rules", summary="口径规则列表")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sem:rule:view")),
):
    rows = (
        await db.execute(select(SemRule).order_by(SemRule.priority.desc(), SemRule.id))
    ).scalars().all()
    return ok(
        [
            {
                "id": r.id, "scene": r.scene, "title": r.title,
                "content": r.content, "priority": r.priority,
                "enabled": bool(r.enabled),
            }
            for r in rows
        ]
    )


@mgmt.get("/fewshots", summary="Few-shot 样本列表")
async def list_fewshots(
    keyword: str = Query("", description="按问题关键词过滤"),
    verified: bool | None = Query(None, description="是否已验证"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_perm("sem:fewshot:view")),
):
    stmt = select(SemFewshot)
    if keyword:
        stmt = stmt.where(SemFewshot.question.ilike(f"%{keyword}%"))
    if verified is not None:
        stmt = stmt.where(SemFewshot.verified.is_(verified))
    total = len((await db.execute(stmt)).scalars().all())
    rows = (
        await db.execute(
            stmt.order_by(SemFewshot.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return ok(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": r.id,
                    "question": r.question,
                    "rewritten": r.rewritten or "",
                    "sql": r.sql_text,
                    "notes": r.notes or "",
                    "hit_count": r.hit_count,
                    "verified": bool(r.verified),
                }
                for r in rows
            ],
        }
    )


# ════════════════════════════════════════════════════════════
# 指标 CRUD
# ════════════════════════════════════════════════════════════
class MetricIn(BaseModel):
    code: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    expr_sql: str
    source_id: int
    unit: str = "万元"
    value_type: str = "decimal"
    agg_default: str = "SUM"
    caliber: str = ""
    default_format: str = ""
    enabled: bool = True


@mgmt.post("/metrics", summary="新增指标")
async def create_metric(
    body: MetricIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:metric:edit")),
):
    # 只与「启用中」的指标查重：停用是软删除，
    # 若把已停用的 code 也算冲突，会让停用后的指标永远无法复用同名重建
    dup = (
        await db.execute(
            select(SemMetric).where(
                SemMetric.code == body.code, SemMetric.enabled.is_(True)
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise AppException(f"指标代码已存在：{body.code}", ErrorCode.CONFLICT, 409)
    row = SemMetric(**body.model_dump())
    db.add(row)
    await db.flush()
    await _audit(db, user, f"新增指标-{body.code}", request)
    await db.commit()
    return ok({"id": row.id})


@mgmt.put("/metrics/{mid}", summary="修改指标")
async def update_metric(
    mid: int,
    body: MetricIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:metric:edit")),
):
    row = await _get(db, SemMetric, mid, "指标")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    await db.flush()
    await _audit(db, user, f"修改指标-{mid}", request)
    await db.commit()
    return ok({"id": mid})


@mgmt.delete("/metrics/{mid}", summary="停用指标（软删除）")
async def delete_metric(
    mid: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:metric:edit")),
):
    row = await _get(db, SemMetric, mid, "指标")
    row.enabled = False
    await db.flush()
    await _audit(db, user, f"停用指标-{mid}", request)
    await db.commit()
    return ok({"id": mid})


# ════════════════════════════════════════════════════════════
# 维度 CRUD
# ════════════════════════════════════════════════════════════
class DimensionIn(BaseModel):
    code: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    expr_sql: str
    display_expr: str = ""
    join_sql: str = ""
    source_id: int
    dim_type: str = "categorical"
    value_map: dict[str, Any] | None = None
    enabled: bool = True


@mgmt.post("/dimensions", summary="新增维度")
async def create_dimension(
    body: DimensionIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:dimension:edit")),
):
    # 同指标：只与启用中的维度查重
    dup = (
        await db.execute(
            select(SemDimension).where(
                SemDimension.code == body.code, SemDimension.enabled.is_(True)
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise AppException(f"维度代码已存在：{body.code}", ErrorCode.CONFLICT, 409)
    row = SemDimension(**body.model_dump())
    db.add(row)
    await db.flush()
    await _audit(db, user, f"新增维度-{body.code}", request)
    await db.commit()
    return ok({"id": row.id})


@mgmt.put("/dimensions/{did}", summary="修改维度")
async def update_dimension(
    did: int,
    body: DimensionIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:dimension:edit")),
):
    row = await _get(db, SemDimension, did, "维度")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    await db.flush()
    await _audit(db, user, f"修改维度-{did}", request)
    await db.commit()
    return ok({"id": did})


@mgmt.delete("/dimensions/{did}", summary="停用维度（软删除）")
async def delete_dimension(
    did: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:dimension:edit")),
):
    row = await _get(db, SemDimension, did, "维度")
    row.enabled = False
    await db.flush()
    await _audit(db, user, f"停用维度-{did}", request)
    await db.commit()
    return ok({"id": did})


# ════════════════════════════════════════════════════════════
# 口径规则 CRUD
# ════════════════════════════════════════════════════════════
class RuleIn(BaseModel):
    scene: str
    title: str
    content: str
    priority: int = 0
    enabled: bool = True


@mgmt.post("/rules", summary="新增口径规则")
async def create_rule(
    body: RuleIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:rule:edit")),
):
    row = SemRule(**body.model_dump())
    db.add(row)
    await db.flush()
    await _audit(db, user, f"新增口径规则-{body.title}", request)
    await db.commit()
    return ok({"id": row.id})


@mgmt.put("/rules/{rid}", summary="修改口径规则")
async def update_rule(
    rid: int,
    body: RuleIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:rule:edit")),
):
    row = await _get(db, SemRule, rid, "口径规则")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    await db.flush()
    await _audit(db, user, f"修改口径规则-{rid}", request)
    await db.commit()
    return ok({"id": rid})


@mgmt.delete("/rules/{rid}", summary="停用口径规则（软删除）")
async def delete_rule(
    rid: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:rule:edit")),
):
    row = await _get(db, SemRule, rid, "口径规则")
    row.enabled = False
    await db.flush()
    await _audit(db, user, f"停用口径规则-{rid}", request)
    await db.commit()
    return ok({"id": rid})


# ════════════════════════════════════════════════════════════
# Few-shot 样本 CRUD
# ════════════════════════════════════════════════════════════
class FewshotIn(BaseModel):
    question: str
    sql: str
    rewritten: str = ""
    notes: str = ""
    verified: bool = False


@mgmt.post("/fewshots", summary="新增 Few-shot 样本")
async def create_fewshot(
    body: FewshotIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:fewshot:edit")),
):
    row = SemFewshot(
        question=body.question,
        rewritten=body.rewritten or None,
        sql_text=body.sql,
        notes=body.notes or None,
        verified=body.verified,
    )
    db.add(row)
    await db.flush()
    await _audit(db, user, f"新增 Few-shot-{row.id}", request)
    await db.commit()
    return ok({"id": row.id})


@mgmt.put("/fewshots/{fid}", summary="修改 Few-shot 样本")
async def update_fewshot(
    fid: int,
    body: FewshotIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:fewshot:edit")),
):
    row = await _get(db, SemFewshot, fid, "Few-shot 样本")
    row.question = body.question
    row.sql_text = body.sql
    row.rewritten = body.rewritten or None
    row.notes = body.notes or None
    row.verified = body.verified
    await db.flush()
    await _audit(db, user, f"修改 Few-shot-{fid}", request)
    await db.commit()
    return ok({"id": fid})


@mgmt.delete("/fewshots/{fid}", summary="删除 Few-shot 样本")
async def delete_fewshot(
    fid: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_perm("sem:fewshot:edit")),
):
    row = await _get(db, SemFewshot, fid, "Few-shot 样本")
    await db.delete(row)
    await db.flush()
    await _audit(db, user, f"删除 Few-shot-{fid}", request)
    await db.commit()
    return ok({"id": fid})


@mgmt.post("/fewshots/{fid}/verify", summary="验证样本 SQL 是否可执行")
async def verify_fewshot(
    fid: int,
    db: AsyncSession = Depends(get_db),
    ro: AsyncSession = Depends(get_readonly_db),
    user: CurrentUser = Depends(require_perm("sem:fewshot:edit")),
):
    """用只读账号真跑一遍样本的 SQL。

    表结构变更后，历史 Few-shot 可能失效（列名改了、视图重建了）；
    失效样本混在 Prompt 里会误导模型，因此提供单条即时验证。
    这里不注入数据权限——验证的是 SQL 本身的可用性，不是某个人的数据范围。
    """
    from sqlalchemy import text as sa_text

    from ...agent.nodes.retrieve import retrieve_schema
    from ...agent.nodes.sql_validate import validate as validate_sql

    row = await _get(db, SemFewshot, fid, "Few-shot 样本")
    schema = await retrieve_schema(db, None)
    try:
        final = validate_sql(row.sql_text, schema.allowed_tables, None, max_rows=5)
        res = await ro.execute(sa_text(final))
        rows = res.fetchall()
    except Exception as exc:  # noqa: BLE001
        return ok({"ok": False, "error": str(exc)[:300]})

    return ok({"ok": True, "rows": len(rows), "sql": final})
