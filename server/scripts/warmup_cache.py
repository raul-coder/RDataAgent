"""问数缓存预热（T5-7）。

为什么需要：
    压测对比很明显——冷缓存 P95 21.5s，预热后 P95 0.1s（吞吐从 0.56 → 111 次/秒）。
    演示或压测前跑一次，可让常见问题全部命中缓存。

预热哪些问题：
    直接取语义层的 Few-shot 样本——它们本来就是真实业务问法，
    预热后对用户实际提问的命中率最高（而不是拍脑袋编一批问题）。

用法：
    python -m scripts.warmup_cache                      # 预热前 30 条，并发 5
    python -m scripts.warmup_cache --limit 50 --concurrency 8
    python -m scripts.warmup_cache --clear              # 只清空缓存
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"
PASSWORD = "123456"


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


async def main_async(
    base: str, username: str, limit: int, concurrency: int, clear_only: bool
) -> int:
    from app.services import qa_cache

    if clear_only:
        n = qa_cache.clear()
        print(f"已清空问数缓存（{n} 个键）")
        return 0

    async with httpx.AsyncClient(base_url=base.rstrip("/"), timeout=180) as c:
        r = await c.post(
            "/auth/login", json={"username": username, "password": PASSWORD}
        )
        if r.status_code != 200:
            print(f"登录失败：HTTP {r.status_code}")
            return 1
        token = r.json()["data"]["access_token"]

        # 取 Few-shot 的问题作为预热语料
        r = await c.get(
            "/semantic/fewshots",
            params={"page_size": limit},
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            print(f"读取 Few-shot 失败：HTTP {r.status_code} {r.text[:120]}")
            print("（该接口需要 sem:fewshot:view 权限，请确认账号具备语义层权限）")
            return 1
        questions = [it["question"] for it in r.json()["data"]["items"]]

    print(f"预热 {len(questions)} 条问题（取自 Few-shot），并发 {concurrency}")

    sem = asyncio.Semaphore(concurrency)
    done = {"ok": 0, "fail": 0}

    async def warm(q: str) -> None:
        async with sem:
            async with httpx.AsyncClient(
                base_url=base, timeout=180,
                headers={"Authorization": f"Bearer {token}"},
            ) as wc:
                try:
                    r = await wc.post(
                        "/chat/completions",
                        json={"content": q},
                        headers={"Accept": "text/event-stream"},
                    )
                    if r.status_code == 200:
                        done["ok"] += 1
                        rows = next(
                            (d.get("rows", 0) for n, d in parse_sse(r.text) if n == "done"), 0
                        )
                        print(f"  ✓ {q[:32]:<34} {rows} 行")
                    else:
                        done["fail"] += 1
                        print(f"  ✗ {q[:32]:<34} HTTP {r.status_code}")
                except Exception as exc:  # noqa: BLE001
                    done["fail"] += 1
                    print(f"  ✗ {q[:32]:<34} {exc}")

    t0 = time.perf_counter()
    await asyncio.gather(*(warm(q) for q in questions))
    cost = time.perf_counter() - t0

    print(f"\n预热完成：成功 {done['ok']}  失败 {done['fail']}  耗时 {cost:.1f}s")
    print(f"缓存 TTL：{qa_cache.settings.QA_CACHE_TTL}s，到期后需重新预热")
    return 0 if done["fail"] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="问数缓存预热")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--username", default="admin")
    ap.add_argument("--limit", type=int, default=30, help="预热问题条数")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--clear", action="store_true", help="只清空缓存")
    args = ap.parse_args()
    return asyncio.run(
        main_async(
            args.base_url, args.username, args.limit, args.concurrency, args.clear
        )
    )


if __name__ == "__main__":
    sys.exit(main())
