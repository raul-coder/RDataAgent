"""模型路由：按场景选择 Provider，并在失败时沿降级链重试。

降级链：
    主模型（LiteLLM，需配置 LLM_API_KEY）
      → fallback 模型（settings.LLM_FALLBACK_MODELS）
      → 检索式兜底（TemplateProvider，无需 Key，命中 verified 样本时准确率极高）
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

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

    # Few-shot 只用于**最后兜底**（TemplateProvider），读不到不该拖垮整次问数。
    # 这里必须保护：它走的是同一个 db 会话，若会话因前序语句失败而处于
    # aborted 状态（PostgreSQL 特性），查询会抛 InFailedSQLTransactionError。
    # 样本为空时 TemplateProvider 会正常报错并被降级链跳过，语义正确。
    try:
        fewshots = await load_fewshots(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 Few-shot 样本失败，检索式兜底本次不可用：%s", exc)
        fewshots = []
    providers.append(TemplateProvider(fewshots))
    return providers


async def complete(
    providers: Sequence[LLMProvider],
    messages: list[LLMMessage],
    *,
    question: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 2048,
    json_mode: bool = False,
    validate: Optional[Callable[[str], Optional[str]]] = None,
) -> LLMResponse:
    """依次尝试 Provider，直到成功；全部失败抛出 LLMError。

    :param validate: 可选的**输出结构校验**。返回 ``None`` 表示通过，
        返回字符串表示失败原因。

        为什么需要它：本函数原来的判定是「首个返回非空内容即采纳」。
        弱模型会返回**非空但完全无效**的内容（例如要求输出 SQL，
        它回 ``{"question":..., "answer":"请提供正确的 SQL 语句。"}``），
        被采纳后就是一条静默的错误结果——比直接失败更难发现。

        加上校验后，输出不合规的 Provider 会被视为失败并继续降级，
        而不是把垃圾输出当成答案。
    """
    errors: list[str] = []
    for idx, p in enumerate(providers):
        try:
            resp = await p.chat(
                messages,
                question=question,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
            if not resp.content:
                errors.append(f"{type(p).__name__}: 返回空内容")
                continue

            if validate is not None:
                reason = validate(resp.content)
                if reason:
                    errors.append(f"{type(p).__name__}: 输出未通过结构校验（{reason}）")
                    logger.warning(
                        "模型输出未通过结构校验，尝试下一个：%s -> %s",
                        type(p).__name__, reason,
                    )
                    continue

            resp.provider = type(p).__name__
            resp.fallback = idx > 0
            return resp
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
