"""结论生成测试：重点锁住「无依据的数据权限提示」的兜底清理。

背景：compose 的 System Prompt 里曾把示例写成
「注：以上结果已按您的数据权限范围过滤（仅含：上海代表处、浙江代表处）。」，
模型把它当模板套用——历史数据里 30 条带该备注的回答中，有 6 条 SQL 根本没有
unit_code 过滤（全是管理员账号）。声称被过滤、实际是全量，属于数据可信度问题。
"""

from __future__ import annotations

import pytest

from app.agent.nodes.compose import strip_false_permission_note

NOTE = "注：以上结果已按您的数据权限范围过滤（仅含：上海代表处、浙江代表处）。"
REAL_PERM = "已按您的数据权限范围过滤（仅含：上海代表处、浙江代表处）"


def test_strip_when_no_permission_note():
    """未提供权限说明时，凭空出现的提示必须被剔除。"""
    text = f"2026年返利合同共 1,185 份。\n\n{NOTE}"
    out = strip_false_permission_note(text, "")
    assert "数据权限" not in out
    assert "1,185" in out          # 正文不能误伤


def test_keep_when_permission_note_provided():
    """确实提供了权限说明时是合法提示，不能删。"""
    text = f"共 2 行数据。\n\n{NOTE}"
    assert strip_false_permission_note(text, REAL_PERM) == text


def test_no_note_untouched():
    text = "上海代表处收入 14,476.80 万元。\n浙江代表处 10,819.20 万元。"
    assert strip_false_permission_note(text, "") == text


def test_strip_multiple_occurrences():
    text = f"开头。\n{NOTE}\n中间内容。\n{NOTE}\n结尾。"
    out = strip_false_permission_note(text, "")
    assert "数据权限" not in out
    assert "中间内容" in out


def test_strip_mid_sentence_only_removes_the_note_line():
    """只删提示本身，前面的句子要保留。"""
    text = f"各产品线收入如下。\n{NOTE}\n建议关注智能计算。"
    out = strip_false_permission_note(text, "")
    assert "各产品线收入如下" in out
    assert "建议关注智能计算" in out
    assert NOTE not in out


def test_collapse_leftover_blank_lines():
    text = f"第一段。\n\n{NOTE}\n\n\n第二段。"
    out = strip_false_permission_note(text, "")
    assert "\n\n\n" not in out
    assert "第一段" in out and "第二段" in out


@pytest.mark.parametrize("text", ["", "   ", "普通结论。"])
def test_empty_or_plain(text):
    assert strip_false_permission_note(text, "") == text.strip() if text.strip() else True
