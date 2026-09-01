"""模型路由：按场景选择 Provider，并在失败时沿降级链重试。

降级链：
    主模型（LiteLLM，需配置 LLM_API_KEY）
      → fallback 模型（settings.LLM_FALLBACK_MODELS）
      → 检索式兜底（TemplateProvider，无需 Key，命中 verified 样本时准确率极高）
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging import get_logger
from .litellm_provider import LitellmProvider
from .provider import LLMError, LLMMessage, LLMProvider, LLMResponse
from .template_provider import TemplateProvider

logger = get_logger(__name__)


async def load_fewshots(db: AsyncSession) -> list[dict]:
    """从语义层读取问答-SQL 样本（verified 优先）。"""
    from ..models import SemFewshot

    rows = (
        await db.execute(
            select(
                SemFewshot.id,
                SemFewshot.question,
                SemFewshot.rewritten,
                SemFewshot.sql_text,
                SemFewshot.verified,
            ).order_by(SemFewshot.verified.desc(), SemFewshot.id)
        )
    ).all()
    return [
        {
            "id": r[0],
            "question": r[1],
            "rewritten": r[2],
            "sql_text": r[3],
            "verified": bool(r[4]),
        }
        for r in rows
    ]


async def build_providers(db: AsyncSession) -> list[LLMProvider]:
    """构造降级链。

    优先级：**数据库「模型配置」> 环境变量 .env**。
    管理员在系统管理页新增/切换默认模型后无需重启即可生效；
    未配置任何模型时才回退到 .env，保证开箱即用。

    数据库读取失败不能拖垮问数，因此捕获后静默回退。
    """
    from ..services import model_service

    providers: list[LLMProvider] = []

    configured: list[dict] = []
    try:
        configured = await model_service.active_models(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取模型配置失败，回退到环境变量配置：%s", exc)

    if configured:
        for m in configured:
            providers.append(
                LitellmProvider(
                    model=m["model_name"],
                    api_key=m.get("api_key") or None,
                    base_url=m["base_url"],
                )
            )
    elif settings.LLM_API_KEY or settings.LLM_DEFAULT_PROVIDER == "ollama":
        providers.append(LitellmProvider())
        for m in settings.fallback_models:
            providers.append(LitellmProvider(model=m))

    providers.append(TemplateProvider(await load_fewshots(db)))
    return providers


async def complete(
    providers: Sequence[LLMProvider],
    messages: list[LLMMessage],
    *,
    question: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 2048,
    json_mode: bool = False,
) -> LLMResponse:
    """依次尝试 Provider，直到成功；全部失败抛出 LLMError。"""
    errors: list[str] = []
    for p in providers:
        try:
            resp = await p.chat(
                messages,
                question=question,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
            if resp.content:
                return resp
            errors.append(f"{type(p).__name__}: 返回空内容")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(p).__name__}: {exc}")
            logger.warning("模型调用失败，尝试下一个：%s", exc)

    raise LLMError("全部模型均调用失败 -> " + " | ".join(errors))


async def stream_complete(
    providers: Sequence[LLMProvider],
    messages: list[LLMMessage],
    *,
    question: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 2048,
):
    """流式输出：优先用第一个可用的 Provider。"""
    errors: list[str] = []
    for p in providers:
        try:
            async for delta in p.stream(
                messages,
                question=question,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield delta
            return
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(p).__name__}: {exc}")
            logger.warning("流式模型调用失败，尝试下一个：%s", exc)
    raise LLMError("全部模型均调用失败 -> " + " | ".join(errors))


def describe(providers: Sequence[LLMProvider]) -> str:
    return " → ".join(type(p).__name__ for p in providers)


def active_model(providers: Sequence[LLMProvider]) -> Optional[str]:
    for p in providers:
        if isinstance(p, LitellmProvider):
            return p.default_model
    return "template-fewshot"
