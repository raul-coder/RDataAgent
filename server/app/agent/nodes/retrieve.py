"""Schema 检索：把语义层元数据裁剪成喂给模型的「精简 Schema」。

为什么不给完整 DDL：
    商业市场台账有 60+ 列，全量给模型会撑爆上下文并诱发列名幻觉。
    这里只输出「被选中的数据源」相关的指标 / 维度 / 口径规则，
    把提示控制在 2k token 内，显著提升 Text2SQL 命中率（见技术方案 §4.3③）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models import SemDataSource, SemDimension, SemMetric, SemRule

logger = get_logger(__name__)

# 表别名约定（写入 Prompt，模型必须遵守）
ALIASES = {
    "bi.fact_contract": "f",
    "bi.fact_ppl": "ppl",
    "bi.fact_goal": "g",
    "bi.dim_unit": "d",
    "bi.dim_industry": "i",
    "bi.dim_product": "p",
    "bi.dim_sales": "s",
    "bi.dim_customer": "c",
}


_JOIN_TABLE_RE = re.compile(r"\bbi\.[a-z_]+", re.I)


@dataclass
class SchemaContext:
    """喂给模型的精简 Schema。"""

    sources: list[dict] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)
    dimensions: list[dict] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    alias_hint: str = ""

    @property
    def allowed_tables(self) -> list[str]:
        """SQL 表白名单 = 数据源对象 + 维表（维表来自维度的 JOIN 语句）。

        维度表必须放行，否则模型写出的 JOIN 会被安全校验误杀。
        """
        names = {s["object_name"] for s in self.sources}
        for d in self.dimensions:
            names.update(_JOIN_TABLE_RE.findall(d.get("join_sql") or ""))
        return sorted(names)

    def render(self) -> str:
        """渲染成 Prompt 片段。"""
        lines: list[str] = []

        lines.append("【可用数据源】")
        for s in self.sources:
            alias = ALIASES.get(s["object_name"], "")
            alias_txt = f"（别名 {alias}）" if alias else ""
            lines.append(
                f"- [{s['group_name']}] {s['name']} → {s['object_name']}{alias_txt}"
                f"：{s['description'] or ''}"
            )

        lines.append("")
        lines.append("【可用指标】（只能使用「表达式」列，禁止臆造列名）")
        for m in self.metrics:
            alias_txt = "、".join(m["aliases"]) if m["aliases"] else "无"
            caliber = f"；口径：{m['caliber']}" if m.get("caliber") else ""
            lines.append(
                f"- {m['name']}（{m['code']}）别名[{alias_txt}] "
                f"表达式 `{m['expr_sql']}` 单位{m['unit']}{caliber}"
            )

        lines.append("")
        lines.append("【可用维度】")
        for d in self.dimensions:
            alias_txt = "、".join(d["aliases"]) if d["aliases"] else "无"
            join = f"；JOIN：`{d['join_sql']}`" if d.get("join_sql") else ""
            disp = f"；展示用 `{d['display_expr']}`" if d.get("display_expr") else ""
            lines.append(
                f"- {d['name']}（{d['code']}）别名[{alias_txt}] "
                f"表达式 `{d['expr_sql']}`{disp}{join}"
            )

        if self.rules:
            lines.append("")
            lines.append("【强制业务规则】")
            for r in self.rules:
                lines.append(f"- [{r['scene']}] {r['content']}")

        if self.alias_hint:
            lines.append("")
            lines.append(self.alias_hint)

        return "\n".join(lines)


async def retrieve_schema(
    db: AsyncSession,
    source_ids: Optional[list[int]] = None,
) -> SchemaContext:
    """按选中的数据源裁剪语义层。source_ids 为空表示全部。"""
    src_stmt = select(SemDataSource).where(SemDataSource.enabled.is_(True))
    if source_ids:
        src_stmt = src_stmt.where(SemDataSource.id.in_(source_ids))
    sources_rows = (await db.execute(src_stmt.order_by(SemDataSource.sort_order))).scalars().all()
    if not sources_rows:
        sources_rows = (
            await db.execute(
                select(SemDataSource)
                .where(SemDataSource.enabled.is_(True))
                .order_by(SemDataSource.sort_order)
            )
        ).scalars().all()

    sids = [s.id for s in sources_rows]

    metrics = (
        await db.execute(
            select(SemMetric).where(SemMetric.enabled.is_(True), SemMetric.source_id.in_(sids))
        )
    ).scalars().all()
    dims = (
        await db.execute(
            select(SemDimension).where(
                SemDimension.enabled.is_(True), SemDimension.source_id.in_(sids)
            )
        )
    ).scalars().all()
    rules = (
        await db.execute(
            select(SemRule).where(SemRule.enabled.is_(True)).order_by(SemRule.priority.desc())
        )
    ).scalars().all()

    ctx = SchemaContext(
        sources=[_src(s) for s in sources_rows],
        metrics=[_metric(m) for m in metrics],
        dimensions=[_dim(d) for d in dims],
        rules=[_rule(r) for r in rules],
        alias_hint=(
            "【别名约定】bi.fact_contract→f、bi.fact_ppl→ppl、bi.fact_goal→g、"
            "bi.dim_unit→d、bi.dim_industry→i、bi.dim_product→p、"
            "bi.dim_sales→s、bi.dim_customer→c。JOIN 维表时必须使用这些别名。"
            "视图（bi.v_*）统一用 v——Few-shot 示例均按此书写，保持一致可避免"
            "同一视图在不同问法下出现多种别名。"
        ),
    )
    logger.info(
        "Schema 检索：%d 数据源 / %d 指标 / %d 维度 / %d 规则",
        len(ctx.sources), len(ctx.metrics), len(ctx.dimensions), len(ctx.rules),
    )
    return ctx


def _src(s: SemDataSource) -> dict:
    return {
        "id": s.id,
        "group_name": s.group_name,
        "name": s.name,
        "object_name": s.object_name,
        "object_type": s.object_type,
        "description": s.description or "",
    }


def _metric(m: SemMetric) -> dict:
    return {
        "code": m.code,
        "name": m.name,
        "aliases": list(m.aliases or []),
        "expr_sql": m.expr_sql,
        "unit": m.unit,
        "agg_default": m.agg_default,
        "caliber": m.caliber or "",
    }


def _dim(d: SemDimension) -> dict:
    # value_map 是多轮改写抽取「主体 / 筛选」的依据，缺了它指代消解会完全失效
    return {
        "code": d.code,
        "name": d.name,
        "aliases": list(d.aliases or []),
        "expr_sql": d.expr_sql,
        "display_expr": d.display_expr or "",
        "join_sql": d.join_sql or "",
        "dim_type": d.dim_type,
        "value_map": d.value_map,
    }


def _rule(r: SemRule) -> dict:
    return {"scene": r.scene, "title": r.title, "content": r.content}


def source_names(ctx: SchemaContext) -> list[str]:
    return [s["name"] for s in ctx.sources]
