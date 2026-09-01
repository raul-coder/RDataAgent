"""问数并发压测（T5-7）。

为什么需要压测而不是只看单次耗时：
    单条问数的耗时主要花在 LLM 上，串行跑看不出系统在多用户下的表现；
    连接池、限流、SSE 推送、权限缓存这些只有并发时才会成为瓶颈。

用法：
    python -m scripts.eval.bench_qa                       # 10 并发 × 2 轮
    python -m scripts.eval.bench_qa --concurrency 30 --rounds 2
    python -m scripts.eval.bench_qa --username zhangsan   # 带数据权限的受限账号
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"
PASSWORD = "123456"

QUESTIONS = [
    "2026年各经营单元收入排名",
    "各产品线收入占比",
    "2026年每月的合同金额趋势",
    "高风险项目有哪些",
    "北京代表处今年达成情况",
    "各行业的收入对比",
    "2026年回款情况",
    "各经营单元目标完成情况",
    "新产品收入占比",
    "各销售人员的业绩排名",
]


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("event: "):
            current = line[7:]
        elif line.startswith("data: "):
            try:
                events.append((current or "", json.loads(line[6:])))
            except json.JSONDecodeError:
                continue
    return events


async def one_round(
    client: httpx.AsyncClient, question: str
) -> tuple[bool, float, int, bool]:
    """发起一次问数，返回 (是否成功, 耗时秒, 结果行数, 是否命中缓存)。"""
    t0 = time.perf_counter()
    try:
        r = await client.post(
            "/chat/completions",
            json={"content": question},
            headers={"Accept": "text/event-stream"},
        )
        cost = time.perf_counter() - t0
        if r.status_code != 200:
            return False, cost, 0, False
        done = next((d for n, d in parse_sse(r.text) if n == "done"), {})
        # 序列化带空格（json.dumps 默认分隔符），两种都兼容
        cached = '"cached": true' in r.text or '"cached":true' in r.text
        return True, cost, int(done.get("rows") or 0), cached
    except Exception:  # noqa: BLE001
        return False, time.perf_counter() - t0, 0, False


async def main_async(
    base: str, username: str, concurrency: int, rounds: int, warmup: int
) -> int:
    async with httpx.AsyncClient(base_url=base.rstrip("/"), timeout=180) as c:
        r = await c.post(
            "/auth/login", json={"username": username, "password": PASSWORD}
        )
        if r.status_code != 200:
            print(f"登录失败：HTTP {r.status_code}")
            return 1
        token = r.json()["data"]["access_token"]
        print(f"账号 {username} · 并发 {concurrency} · 轮次 {rounds} · 共 {concurrency * rounds} 次问数")

        # 预热：把语义层检索、权限缓存、模型连接的冷启动成本排除在统计外
        if warmup:
            print(f"预热 {warmup} 次（不计入统计）…")
            for i in range(warmup):
                await one_round(
                    httpx.AsyncClient(
                        base_url=base, timeout=180,
                        headers={"Authorization": f"Bearer {token}"},
                    ),
                    QUESTIONS[i % len(QUESTIONS)],
                )

        async def worker(wid: int) -> list[tuple[bool, float, int]]:
            out = []
            async with httpx.AsyncClient(
                base_url=base, timeout=180,
                headers={"Authorization": f"Bearer {token}"},
            ) as wc:
                for i in range(rounds):
                    q = QUESTIONS[(wid + i) % len(QUESTIONS)]
                    out.append(await one_round(wc, q))
            return out

        t0 = time.perf_counter()
        results = await asyncio.gather(*(worker(i) for i in range(concurrency)))
        wall = time.perf_counter() - t0

    flat = [r for rs in results for r in rs]
    oks = [c for ok, c, _, _ in flat if ok]
    fails = [f for f in flat if not f[0]]
    rows = [n for ok, _, n, _ in flat if ok]
    hits = [c for _, _, _, c in flat if c]

    print("\n" + "=" * 62)
    print(f"总请求 {len(flat)}  成功 {len(oks)}  失败 {len(fails)}  "
          f"成功率 {len(oks) / len(flat) * 100:.0f}%")
    if oks:
        oks_sorted = sorted(oks)
        p50 = statistics.median(oks_sorted)
        p95 = oks_sorted[min(len(oks_sorted) - 1, int(len(oks_sorted) * 0.95))]
        print(f"耗时  P50 {p50:.1f}s   P95 {p95:.1f}s   "
              f"最小 {min(oks):.1f}s   最大 {max(oks):.1f}s")
        print(f"吞吐  {len(oks) / wall:.2f} 次/秒（总墙钟 {wall:.1f}s）")
        print(f"结果行数  平均 {statistics.mean(rows):.1f} 行" if rows else "")
        print(f"缓存命中  {len(hits)}/{len(flat)}（{len(hits) / len(flat) * 100:.0f}%）")
        print(f"\nNFR-P1 要求 P95 ≤ 8s：{'达标' if p95 <= 8 else f'未达标（{p95:.1f}s）'}")
    if fails:
        print(f"\n失败样本：{fails[:5]}")
    print("=" * 62)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="问数并发压测")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--username", default="admin")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--warmup", type=int, default=2, help="预热次数，不计入统计")
    args = ap.parse_args()
    return asyncio.run(
        main_async(
            args.base_url, args.username, args.concurrency, args.rounds, args.warmup
        )
    )


if __name__ == "__main__":
    sys.exit(main())
