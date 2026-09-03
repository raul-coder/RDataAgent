"""模型路由与输出结构校验测试。

核心要锁住的语义：**输出不合规 == 该 Provider 调用失败**。
否则「首个返回非空内容即采纳」会把弱模型的垃圾输出当成答案——
静默错误比直接失败更难发现。
"""

from __future__ import annotations

import pytest

from app.agent.nodes.slot_llm import validate_slot_output
from app.agent.nodes.sql_generate import validate_sql_output
from app.llm.provider import LLMMessage, LLMResponse
from app.llm.router import complete


class FakeProvider:
    """最小 Provider 实现，避免测试依赖真实模型。"""

    is_remote = True

    def __init__(self, content: str = "", *, raise_exc: bool = False, name: str = "Fake"):
        self.content = content
        self.raise_exc = raise_exc
        self.name = name
        self.calls = 0

    async def chat(self, messages, *, question=None, temperature=0.1,
                   max_tokens=2048, json_mode=False, model=None):
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("模型不可用")
        return LLMResponse(content=self.content, model="fake-model")


MSGS = [LLMMessage(role="user", content="hi")]


# ── SQL 输出校验 ────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    '{"thought":"...","sql":"SELECT 1"}',
    '```json\n{"sql":"WITH a AS (SELECT 1) SELECT * FROM a"}\n```',
    '前缀废话 {"sql": "select f.a from bi.fact_contract f"} 后缀',
])
def test_validate_sql_output_accepts(text):
    assert validate_sql_output(text) is None, text


@pytest.mark.parametrize("text,kw", [
    ("", "JSON"),                                          # 空
    ("这不是 JSON", "JSON"),                                # 非 JSON
    ("{}", "sql"),                                         # 缺 sql
    ('{"thought":"x"}', "sql"),                            # 缺 sql
    ('{"sql":"   "}', "sql"),                              # sql 为空
    ('{"question":"...","answer":"请提供正确的 SQL 语句。"}', "sql"),  # 退化输出
    ('{"sql":"请提供正确的 SQL 语句"}', "查询语句"),          # 非查询语句
])
def test_validate_sql_output_rejects(text, kw):
    reason = validate_sql_output(text)
    assert reason is not None, f"{text} 应被拒绝"
    assert kw in reason, f"{text} 的失败原因应包含 {kw}"


def test_validate_sql_output_does_not_judge_correctness():
    """SQL 语法错误不属于这一层——那是 sql_validate 与自愈重试的职责。

    这里只判断输出是否**残缺**；把语法错也判成 Provider 失败，
    会导致「换个模型重试」，而正确做法是「带上报错让同一个模型改」。
    """
    assert validate_sql_output('{"sql":"SELECT * FROM"}') is None


# ── 槽位输出校验 ────────────────────────────────────────────────────
def test_validate_slot_output():
    assert validate_slot_output('{"metrics":["biz_income"]}') is None
    assert validate_slot_output("无法解析") is not None
    assert validate_slot_output("") is not None


# ── 降级链行为 ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_complete_returns_first_valid_and_marks_not_fallback():
    p1, p2 = FakeProvider('{"sql":"SELECT 1"}'), FakeProvider('{"sql":"SELECT 2"}')
    resp = await complete([p1, p2], MSGS, validate=validate_sql_output)
    assert resp.content == '{"sql":"SELECT 1"}'
    assert resp.fallback is False
    assert p2.calls == 0          # 首选可用就不该调用备用


@pytest.mark.asyncio
async def test_complete_skips_provider_failing_validation():
    """关键用例：主模型返回非空但无效的内容，必须继续降级而不是采纳。"""
    bad = FakeProvider('{"question":"x","answer":"请提供正确的 SQL 语句。"}')
    good = FakeProvider('{"sql":"SELECT 1"}')
    resp = await complete([bad, good], MSGS, validate=validate_sql_output)
    assert resp.content == '{"sql":"SELECT 1"}'
    assert resp.fallback is True   # 如实标记：这条答案来自备用模型
    assert resp.provider == "FakeProvider"


@pytest.mark.asyncio
async def test_complete_skips_empty_content():
    empty = FakeProvider("")
    good = FakeProvider('{"sql":"SELECT 1"}')
    resp = await complete([empty, good], MSGS, validate=validate_sql_output)
    assert resp.fallback is True


@pytest.mark.asyncio
async def test_complete_skips_raising_provider():
    broken = FakeProvider(raise_exc=True)
    good = FakeProvider('{"sql":"SELECT 1"}')
    resp = await complete([broken, good], MSGS, validate=validate_sql_output)
    assert resp.fallback is True


@pytest.mark.asyncio
async def test_complete_all_invalid_raises():
    """所有模型输出都不合规时应当整体失败，而不是返回最后一个垃圾输出。"""
    from app.llm.provider import LLMError

    providers = [
        FakeProvider('{"answer":"请提供正确的 SQL 语句。"}'),
        FakeProvider("not json at all"),
    ]
    with pytest.raises(LLMError) as exc:
        await complete(providers, MSGS, validate=validate_sql_output)
    assert "结构校验" in str(exc.value)


@pytest.mark.asyncio
async def test_complete_without_validate_keeps_legacy_behaviour():
    """不传 validate 时行为与改造前一致（首个非空即采纳）。"""
    bad = FakeProvider('完全不是 JSON 但非空')
    good = FakeProvider('{"sql":"SELECT 1"}')
    resp = await complete([bad, good], MSGS)
    assert resp.content == "完全不是 JSON 但非空"
    assert resp.fallback is False
