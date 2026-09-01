"""UC-3 数据权限隔离验证。

用例定义（docs/01 需求分析说明书）：
    以「普通用户（仅授权上海代表处、浙江代表处）」登录 → 问「各经营单元收入排名」。
预期：
    SQL 自动注入 unit_code IN ('SH','ZJ')，结果仅含 2 个单元，
    且**回答中明确提示「已按您的数据权限范围过滤」**。

第三点容易被忽略：权限过滤是服务端强制注入的，用户看不到 SQL。
若不提示，查「北京代表处」返回 0 行时用户会以为数据不存在或名字写错，
而不是"我没有权限"。因此本脚本对「结论是否提及权限」做断言。

用法：
    python -m scripts.eval.check_uc3
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
PERM_HINTS = ("数据权限", "权限范围", "权限内")


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


class Session:
    def __init__(self, base: str) -> None:
        self.c = httpx.AsyncClient(base_url=base.rstrip("/"), timeout=120)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def login(self, username: str) -> dict:
        r = await self.c.post(
            "/auth/login", json={"username": username, "password": PASSWORD}
        )
        if r.status_code != 200:
            raise RuntimeError(f"{username} 登录失败：HTTP {r.status_code}")
        data = r.json()["data"]
        self.c.headers["Authorization"] = f"Bearer {data['access_token']}"
        return data["user"]

    async def ask(self, question: str) -> dict:
        """发起一次问数，返回 sql / rows / 结论文本。"""
        r = await self.c.post(
            "/chat/completions",
            json={"content": question},
            headers={"Accept": "text/event-stream"},
        )
        events = parse_sse(r.text)
        sql = next((d.get("sql") for n, d in events if n == "sql"), None)
        done = next((d for n, d in events if n == "done"), {})
        text = "".join(d.get("delta", "") for n, d in events if n == "token")
        rows: list = []
        for n, d in events:
            if n == "table":
                rows = d.get("rows", [])
                break
        return {"sql": sql or "", "rows": rows, "text": text, "done": done}


def step(title: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK  ' if ok else 'FAIL'}] {title}" + (f"  —— {detail}" if detail else ""))
    return ok


async def main_async(base: str, limited_user: str, admin_user: str) -> int:
    results: list[bool] = []

    # ── 受限用户 ──────────────────────────────────────────────────
    limited = Session(base)
    user = await limited.login(limited_user)
    perms = user.get("data_perms", {})
    visible = perms.get("1") or []
    results.append(step("1. 受限用户仅授权部分经营单元",
                        bool(visible), f"data_perms[1] = {visible}"))

    print("   问数中（约 15s）…")
    r1 = await limited.ask("2026年各经营单元收入排名")
    injected = "IN (" in r1["sql"].upper() or "IN('" in r1["sql"].upper().replace(" ", "")
    results.append(step("2. SQL 注入数据权限过滤", injected, r1["sql"][-60:]))
    results.append(step("3. 结果仅含授权单元", len(r1["rows"]) == len(visible),
                        f"{len(r1['rows'])} 行 / 授权 {len(visible)} 个"))
    results.append(step("4. 结论明确提示已按权限过滤",
                        any(h in r1["text"] for h in PERM_HINTS),
                        r1["text"][-70:]))

    # ── 越权查询：应为空且说明原因 ─────────────────────────────────
    print("   问数中（越权场景）…")
    r2 = await limited.ask("北京代表处今年达成情况")
    results.append(step("5. 查询无权单元返回空结果", len(r2["rows"]) == 0,
                        f"{len(r2['rows'])} 行"))
    results.append(step("6. 结论说明是权限所致（而非数据不存在）",
                        any(h in r2["text"] for h in PERM_HINTS),
                        r2["text"][:80]))
    misleading = "名称是否准确" in r2["text"]
    results.append(step("7. 未误导用户去检查名称拼写", not misleading,
                        f"含误导话术={misleading}"))
    await limited.aclose()

    # ── 管理员：不应对其施加过滤 ──────────────────────────────────
    admin = Session(base)
    await admin.login(admin_user)
    print("   问数中（管理员对照）…")
    r3 = await admin.ask("2026年各经营单元收入排名")
    results.append(step("8. 管理员可见全部经营单元", len(r3["rows"]) > len(visible),
                        f"{len(r3['rows'])} 行"))
    results.append(step("9. 管理员结论不含权限提示",
                        not any(h in r3["text"] for h in PERM_HINTS),
                        r3["text"][:60]))
    await admin.aclose()

    passed = sum(results)
    print(f"\nUC-3 通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="UC-3 数据权限隔离验证")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--user", default="zhangsan", help="受限用户")
    ap.add_argument("--admin", default="admin", help="管理员（对照组）")
    args = ap.parse_args()
    t0 = time.perf_counter()
    code = asyncio.run(main_async(args.base_url, args.user, args.admin))
    print(f"耗时 {time.perf_counter() - t0:.1f}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
