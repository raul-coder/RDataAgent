"""检索式兜底 Provider（无需 API Key）。

定位：技术方案 §11.4 的降级路径之一——模型不可用 / 未配置 Key 时，
用「语义层 Few-shot 库 + 关键词打分」检索出最相近的样本，返回其 SQL。

它同时带来两个收益：
  1. 没有 Key 也能完整跑通「改写 → 检索 → 生成 → 校验 → 执行 → 呈现」全链路；
  2. Few-shot 库由人工校验（verified=true），因此命中时准确率接近 100%。
"""

from __future__ import annotations

import re
from typing import AsyncIterator, Optional

from ..core.logging import get_logger
from .provider import LLMMessage, LLMProvider, LLMResponse

logger = get_logger(__name__)

# 中文分词：按 2-gram 切分，无需外部分词库
_STOP = set("的了和与及对是在有我你他年月日个多少请帮我查询看看一下有哪些吗呢吧")


def _tokens(text: str) -> set[str]:
    text = re.sub(r"[^\w\u4e00-\u9fa5]+", " ", text or "")
    compact = text.replace(" ", "")
    grams = {compact[i : i + 2] for i in range(len(compact) - 1)}
    grams |= {w for w in re.split(r"\s+", text) if w and w not in _STOP}
    return grams


def similarity(q1: str, q2: str) -> float:
    """基于 2-gram 的 Jaccard 相似度。"""
    a, b = _tokens(q1), _tokens(q2)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class TemplateProvider(LLMProvider):
    """用 Few-shot 库做检索式生成（同步实现，接口保持 async）。"""

    is_remote = False

    def __init__(self, fewshots: list[dict], model_name: str = "template-fewshot") -> None:
        self.fewshots = fewshots
        self.model_name = model_name

    def best_match(self, question: str) -> tuple[Optional[dict], float]:
        best, best_score = None, 0.0
        for fs in self.fewshots:
            score = similarity(question, fs.get("question", ""))
            rw = fs.get("rewritten")
            if rw:
                score = max(score, similarity(question, rw))
            # 人工校验样本优先
            if fs.get("verified"):
                score *= 1.05
            if score > best_score:
                best, best_score = fs, score
        return best, best_score

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
        # 必须用显式传入的原始问题：messages 里的 user 内容是拼接后的长 Prompt，
        # 直接拿去匹配会把相似度稀释到接近 0。
        if not question:
            raise ValueError("缺少原始问题，无法进行检索式匹配")

        best, score = self.best_match(question)
        if not best or score < 0.18:
            raise ValueError(f"未匹配到可用样本（最高相似度 {score:.2f}）")

        logger.info("检索式生成命中样本 #%s（相似度 %.2f）", best.get("id"), score)

        if json_mode:
            import json

            content = json.dumps(
                {
                    "thought": f"命中人工校验样本「{best.get('question')}」，直接复用其取数逻辑",
                    "sql": best.get("sql_text", ""),
                    "chart": {"type": "auto"},
                    "confidence": round(min(0.95, 0.6 + score), 2),
                },
                ensure_ascii=False,
            )
        else:
            content = best.get("sql_text", "")

        return LLMResponse(
            content=content,
            model=self.model_name,
            prompt_tokens=len(question) // 2,
            completion_tokens=len(content) // 2,
            raw={"fewshot_id": best.get("id"), "score": round(score, 3)},
        )

    async def stream(self, messages: list[LLMMessage], **kwargs) -> AsyncIterator[str]:
        resp = await self.chat(messages, **kwargs)
        # 模拟流式：按 12 字一片吐出
        text = resp.content
        for i in range(0, len(text), 12):
            yield text[i : i + 12]
