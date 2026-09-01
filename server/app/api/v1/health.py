"""健康检查与元信息接口（I0 验收用：证明服务、数据库、数据、语义层均已就绪）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import Settings, get_settings
from ...core.exceptions import AppException, ErrorCode
from ...db.session import get_readonly_db

router = APIRouter(tags=["health"])


@router.get("/health", summary="健康检查")
async def health(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "code": ErrorCode.OK,
        "message": "ok",
        "data": {
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "env": settings.ENV,
            "data_as_of": settings.DATA_AS_OF,
            "default_year": settings.DEFAULT_YEAR,
        },
        "trace_id": "-",
    }


@router.get("/health/db", summary="数据库连通性检查")
async def health_db(session: AsyncSession = Depends(get_readonly_db)) -> dict:
    try:
        row = (await session.execute(text("SELECT version()"))).scalar_one()
        return {"code": ErrorCode.OK, "message": "ok", "data": {"version": row}, "trace_id": "-"}
    except Exception as exc:  # noqa: BLE001
        raise AppException(
            f"数据库连接失败：{exc}", ErrorCode.INTERNAL, 500
        ) from exc


@router.get("/meta/overview", summary="数据总览（事实表行数 + 语义层规模）")
async def meta_overview(session: AsyncSession = Depends(get_readonly_db)) -> dict:
    async def scalar(sql: str):
        return (await session.execute(text(sql))).scalar() or 0

    data = {
        "fact_contract": await scalar("SELECT COUNT(*) FROM bi.fact_contract"),
        "fact_ppl": await scalar("SELECT COUNT(*) FROM bi.fact_ppl"),
        "fact_goal": await scalar("SELECT COUNT(*) FROM bi.fact_goal"),
        "dim_unit": await scalar("SELECT COUNT(*) FROM bi.dim_unit"),
        "sem_data_source": await scalar("SELECT COUNT(*) FROM sem_data_source"),
        "sem_metric": await scalar("SELECT COUNT(*) FROM sem_metric"),
        "sem_dimension": await scalar("SELECT COUNT(*) FROM sem_dimension"),
        "sem_rule": await scalar("SELECT COUNT(*) FROM sem_rule"),
        "sem_fewshot": await scalar("SELECT COUNT(*) FROM sem_fewshot"),
    }
    return {"code": ErrorCode.OK, "message": "ok", "data": data, "trace_id": "-"}


@router.get("/meta/achieve", summary="2026 年各经营单元达成概览（验证造数合理性）")
async def meta_achieve(session: AsyncSession = Depends(get_readonly_db)) -> dict:
    rows = (
        await session.execute(
            text(
                """
                SELECT unit_name, biz_goal, income, achieve_rate, is_warning
                FROM bi.v_overall_achieve
                WHERE year = 2026
                ORDER BY achieve_rate DESC NULLS LAST
                """
            )
        )
    ).all()
    data = [
        {
            "unit": r[0],
            "biz_goal": float(r[1]),
            "income": float(r[2]),
            "achieve_rate": float(r[3]) if r[3] is not None else None,
            "warning": bool(r[4]),
        }
        for r in rows
    ]
    return {"code": ErrorCode.OK, "message": "ok", "data": data, "trace_id": "-"}
