"""LiteLLM 提供者（OpenAI 兼容协议，支持 DeepSeek / Qwen / GLM / Moonshot / OpenAI / Claude / Ollama）。"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from ..core.config import settings
from ..core.logging import get_logger
from .provider import LLMError, LLMMessage, LLMProvider, LLMResponse

logger = get_logger(__name__)


def resolve_model_name(base_url: str, model: str) -> str:
    """推断 litellm 需要的模型名前缀。

    配置了自定义 base_url 时（各家基本都提供 OpenAI 兼容端点）统一按 openai 协议；
    已自带 provider 前缀的（如 ``ollama/qwen2.5:7b``）保持原样。

    「模型配置」页的连通性测试与真实调用共用此函数，
    避免出现「测试通过、真跑报错」这类两端不一致的问题。
    """
    if base_url and "/" not in model:
        return f"openai/{model}"
    return model


class LitellmProvider(LLMProvider):
    """通过 litellm 统一调用各家模型。

    仅在配置了 LLM_API_KEY（或 provider 为 ollama 等本地服务）时可用。
    """

    is_remote = True

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.default_model = model or settings.LLM_DEFAULT_MODEL
        self.api_key = api_key or settings.LLM_API_KEY
        self.base_url = base_url or settings.LLM_BASE_URL

    def _resolve(self, model: Optional[str]) -> str:
        return resolve_model_name(self.base_url, model or self.default_model)

    def _kwargs(self, model: str, **extra: Any) -> dict:
        kw: dict[str, Any] = {"model": model, "api_key": self.api_key or None}
        if self.base_url:
            kw["api_base"] = self.base_url
        kw.update(extra)
        return kw

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        question: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        json_mode: bool = False,
        model: Optional[str] = None,
    ) -> LLMResponse:
        try:
            import litellm  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise LLMError("未安装 litellm，无法调用真实模型") from exc

        m = self._resolve(model)
        extra: dict[str, Any] = {"temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}

        try:
            resp = await litellm.acompletion(
                messages=[{"role": x.role, "content": x.content} for x in messages],
                **self._kwargs(m, **extra),
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"模型 {m} 调用失败：{exc}") from exc

        usage = getattr(resp, "usage", None)
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            model=m,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            finish_reason=getattr(resp.choices[0], "finish_reason", "stop") or "stop",
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        question: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        try:
            import litellm  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise LLMError("未安装 litellm，无法调用真实模型") from exc

        m = self._resolve(model)
        try:
            resp = await litellm.acompletion(
                messages=[{"role": x.role, "content": x.content} for x in messages],
                stream=True,
                **self._kwargs(m, temperature=temperature, max_tokens=max_tokens),
            )
            async for chunk in resp:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"模型 {m} 流式调用失败：{exc}") from exc
