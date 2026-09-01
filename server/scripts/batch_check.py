"""问数批量回归：跑一批问题，收集 SQL / 报错 / 结果，用于发现语义层缺口。

与 check_semantic.py 的分工：
    check_semantic  静态体检 —— 语义层配方本身对不对
    batch_check     动态实测 —— 模型拿到配方后会不会出错

为什么需要它：
    语义层配方可能完全正确，但覆盖不足（某张表的列没被提及），
    模型遇到没覆盖的问题就会硬凑，生成不存在的列或直接报错。
    这类问题只有真跑一遍才会暴露。

用法：
    cd server && .venv/bin/python -m scripts.batch_check
    .venv/bin/python -m scripts.batch_check --batch 2      # 只跑第 2 批
    .venv/bin/python -m scripts.batch_check --no-cache      # 先清空问数缓存

判定：
    OK      —— 有结果行且无错误
    NO_DATA —— 未报错但结果为空（可能是条件过严，也可能是语义理解错了）
    ERROR   —— 执行报错（SQL 错误 / 拒绝 / 异常）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"
USERNAME = "admin"
PASSWORD = "123456"

# 分批：每批一个主题，便于定位是哪一类能力缺失
BATCHES: dict[int, tuple[str, list[str]]] = {
    1: ("核心指标", [
        "2026年商业收入是多少",
        "2026年商业目标合计",
        "2026年回款情况",
        "2026年整体完成率",
    ]),
    2: ("维度拆分", [
        "2026年各产品线收入",
        "2026年各经营单元收入排名",
        "2026年各行业收入分布",
        "2026年各区域收入对比",
    ]),
    3: ("时间维度", [
        "2026年商业目标按月趋势",
        "2026年各季度收入",
        "近3个月收入情况",
        "2026年10月收入",
    ]),
    4: ("对比与专项", [
        "2026年各产品线收入同比",
        "2026年商解收入占比",
        "高风险项目有哪些",
        "2026年重点单元达成情况",
    ]),
    # 第 5 批：语义层未覆盖的列（check_semantic 覆盖度报告指出的缺口）。
    # 模型看不到这些列的定义，问到就只能猜——用来验证「猜」的后果。
    5: ("边缘能力·未覆盖列", [
        "2026年毛利是多少",
        "2026年合同数量",
        "2026年各销售员收入排名",
        "2026年各产品型号收入",
    ]),
}


async def _login(client: httpx.AsyncClient) -> str:
    r = await client.post(f"{API}/auth/login", json={"username": USERNAME, "password": PASSWORD})
    r.raise_for_status()
    return (r.json().get("data") or {})["access_token"]


async def _new_session(client: httpx.AsyncClient, token: str, title: str) -> int:
    r = await client.post(
        f"{API}/chat/sessions",
        json={"title": title},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return (r.json().get("data") or {})["id"]


async def _ask(client: httpx.AsyncClient, token: str, session_id: int, question: str) -> dict[str, Any]:
    """发问并解析 SSE，返回 sql / error / rows / cached / cost。"""
    out: dict[str, Any] = {"sql": "", "error": "", "rows": 0, "cached": False, "ms": 0}
    t0 = time.time()
    async with client.stream(
        "POST",
        f"{API}/chat/completions",
        json={"content": question, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=180.0,
    ) as resp:
        event = ""
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                raw = line[5:].strip()
                try:
                    d = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                if event == "sql":
                    out["sql"] = d.get("sql", "")
                elif event == "error":
                    out["error"] = str(d.get("message", ""))[:160]
                elif event == "table":
                    out["rows"] = len(d.get("rows") or [])
                elif event == "done":
                    out["ms"] = int(d.get("cost_ms") or 0)
    out["wall_ms"] = int((time.time() - t0) * 1000)
    return out


def _verdict(r: dict[str, Any]) -> str:
    if r["error"]:
        return "ERROR"
    if r["rows"] == 0:
        return "NO_DATA"
    return "OK"


async def run(batch_no: int | None, clear_cache: bool) -> int:
    if clear_cache:
        from app.core.config import settings
        from app.services import qa_cache

        if not settings.QA_CACHE_TTL or qa_cache.redis.is_degraded():
            print("缓存未启用或 Redis 降级，跳过清空")
        else:
            n = qa_cache.clear()
            print(f"已清空问数缓存（{n} 条）\n")

    targets = [BATCHES[batch_no]] if batch_no else [BATCHES[k] for k in sorted(BATCHES)]

    async with httpx.AsyncClient() as client:
        token = await _login(client)
        bad = 0
        for name, questions in targets:
            print("=" * 78)
            print(f"批次：{name}")
            print("=" * 78)
            for q in questions:
                sid = await _new_session(client, token, f"批量验证-{q[:12]}")
                try:
                    r = await _ask(client, token, sid, q)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [请求异常] {q}\n      └─ {exc}")
                    bad += 1
                    continue
                v = _verdict(r)
                if v != "OK":
                    bad += 1
                mark = {"OK": "✅", "NO_DATA": "⚠️ ", "ERROR": "❌"}[v]
                print(f"  {mark} {q}  ({r['rows']} 行 / {r['wall_ms']}ms)")
                if r["sql"]:
                    print(f"      SQL: {r['sql'][:150]}")
                if r["error"]:
                    print(f"      错误: {r['error']}")
                if v == "NO_DATA":
                    print("      └─ 无数据行：确认是条件过严，还是语义理解错了")
            print()

    print("=" * 78)
    print(f"完成，异常 {bad} 项" if bad else "完成，全部通过 ✅")
    print("=" * 78)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="问数批量回归")
    ap.add_argument("--batch", type=int, choices=sorted(BATCHES), help="只跑指定批次")
    ap.add_argument("--no-cache", action="store_true", help="先清空问数缓存")
    args = ap.parse_args()
    return asyncio.run(run(args.batch, args.no_cache))


if __name__ == "__main__":
    sys.exit(main())
