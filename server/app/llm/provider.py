"""LLM 抽象层。

设计要点：
1. 所有模型调用统一走 LLMProvider 接口，业务代码不感知具体厂商；
2. 支持 JSON 模式（强制结构化输出）与流式输出；
3. 支持降级链：主模型失败 → 依次尝试 fallback（见 router.py）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional


@dataclass
class LLMMessage:
    role: str  # system / user / assistant
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    raw: Any = field(default=None, repr=False)
    #: 实际响应本次调用的 Provider 类名（降级链上可能是第 2、3 个）
    provider: str = ""
    #: 是否由降级链中的「非首选」Provider 响应。
    #: 用于向用户如实说明「这条答案不是主模型给出的」。
    fallback: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMError(Exception):
    """模型调用失败（可被降级链捕获）。"""


class LLMProvider(ABC):
    """大模型提供者接口。"""

    #: 是否真实调用外部模型（False 表示本地/规则实现，用于降级与离线演示）
    is_remote: bool = True

    @abstractmethod
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
        """非流式对话。

        :param question: 原始用户问题。真实模型可忽略；检索式实现需要它
            做样本匹配——不能从 messages 里猜，因为 user 消息是拼接后的长 Prompt。
        """

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        question: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """流式对话；默认实现退化为一次性返回。"""
        resp = await self.chat(
            messages,
            question=question,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )
        yield resp.content


def extract_json(text: str) -> Optional[dict]:
    """从模型输出中提取 JSON 对象（兼容 ```json 代码块与前后废话）。"""
    import json
    import re

    text = (text or "").strip()
    # 去掉 markdown 代码块
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    # 截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None
