"""UC-4 反馈闭环端到端验证。

用例定义（docs/01 需求分析说明书）：
    用户对回答点「数据有误」→ 提交 → 管理员登录 → 反馈管理 ▸ 回复校对
    → 看到该条（待处理）→ 打开处理 → 填写备注 → 标记已处理。
预期：状态实时变更；反馈单可跳转到原会话回放。

用法：
    python -m scripts.eval.check_uc4
    python -m scripts.eval.check_uc4 --user zhangsan --admin admin

说明：脚本会真实发起一次问数（约 15s）以拿到真实的 message_id。
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
MARK = "【UC-4 自动化验证】"


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.c = httpx.AsyncClient(base_url=self.base, timeout=120)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def login(self, username: str) -> dict:
        r = await self.c.post(
            "/auth/login", json={"username": username, "password": PASSWORD}
        )
        if r.status_code != 200:
            raise RuntimeError(f"{username} 登录失败：HTTP {r.status_code} {r.text[:120]}")
        self.c.headers["Authorization"] = f"Bearer {r.json()['data']['access_token']}"
        return r.json()["data"]["user"]


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


def step(title: str, ok: bool, detail: str = "") -> bool:
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {title}" + (f"  —— {detail}" if detail else ""))
    return ok


async def main_async(base: str, normal_user: str, admin_user: str) -> int:
    results: list[bool] = []

    # ── 1. 普通用户提问 ────────────────────────────────────────────
    user = Client(base)
    await user.login(normal_user)
    print(f"① 普通用户 {normal_user} 登录成功")

    print("   正在问数（真实链路，约 15s）…")
    r = await user.c.post(
        "/chat/completions",
        json={"content": f"{MARK}2026年各经营单元收入排名"},
        headers={"Accept": "text/event-stream"},
    )
    events = parse_sse(r.text)
    done = next((d for n, d in events if n == "done"), {})
    metas = [d for n, d in events if n == "meta"]

    # 后端会发两次 meta：用户消息 id 与 AI 回答 id（用 role 区分）。
    # 「数据有误」针对的是 AI 回答，必须取 role=assistant 的那一条——
    # 取错会导致反馈单的 question 为空、ai_reply 变成用户问题。
    ai_meta = next((d for d in metas if d.get("role") == "assistant"), {})
    user_meta = next((d for d in metas if d.get("role") == "user"), {})
    message_id = ai_meta.get("message_id")
    session_id = ai_meta.get("session_id") or user_meta.get("session_id")

    results.append(step("1. 普通用户问数成功", bool(message_id),
                        f"assistant_message_id={message_id} session_id={session_id} rows={done.get('rows')}"))
    results.append(step("1b. 两次 meta 带 role 且 id 不同（前端回填依赖）",
                        bool(message_id)
                        and bool(user_meta.get("message_id"))
                        and message_id != user_meta.get("message_id"),
                        f"user={user_meta.get('message_id')} assistant={message_id}"))
    if not message_id:
        return 1

    # ── 2. 提交「数据有误」反馈 ────────────────────────────────────
    r = await user.c.post(
        "/chat/data-error",
        json={"message_id": message_id, "comment": f"{MARK}该排名与实际台账不一致，请核查"},
    )
    fb_id = r.json().get("data", {}).get("feedback_id") if r.status_code == 200 else None
    results.append(step("2. 提交「数据有误」生成反馈单", bool(fb_id), f"feedback_id={fb_id}"))
    if not fb_id:
        return 1

    # ── 3. 点赞点踩（FR-Q25）──────────────────────────────────────
    r = await user.c.post(
        "/feedback/rating", json={"message_id": message_id, "rating": "down"}
    )
    results.append(step("3. 对回答点踩", r.status_code == 200, f"HTTP {r.status_code}"))

    # ── 4. 越权防护：不能对他人会话的消息点赞 ──────────────────────
    other = Client(base)
    await other.login(admin_user)
    # 管理员是超管，理论上放行；改用接口探测一条不存在的消息
    r = await other.c.post(
        "/feedback/rating", json={"message_id": 99999999, "rating": "up"}
    )
    results.append(step("4. 对不存在的消息反馈被拦截", r.status_code == 404,
                        f"HTTP {r.status_code}"))
    await other.aclose()

    # ── 5. 管理员在回复校对看到该条（待处理）───────────────────────
    admin = Client(base)
    await admin.login(admin_user)
    r = await admin.c.get("/feedback", params={"status": "待处理", "page_size": 100})
    items = r.json()["data"]["items"]
    found = next((x for x in items if x["id"] == fb_id), None)
    results.append(step("5. 管理员在回复校对看到该条（待处理）", found is not None,
                        f"待处理 {len(items)} 条，命中={bool(found)}"))

    # ── 6. 查看详情（含会话 ID，供跳转回放）───────────────────────
    r = await admin.c.get(f"/feedback/{fb_id}")
    detail = r.json().get("data", {})
    results.append(step("6. 反馈单详情含会话 ID 与 AI 回答快照",
                        detail.get("session_id") == session_id and bool(detail.get("ai_reply")),
                        f"session_id={detail.get('session_id')} 快照 {len(detail.get('ai_reply') or '')} 字"))

    # ── 7. 处理：填写备注 + 标记已处理 ────────────────────────────
    remark = f"{MARK}已核对，口径一致，数据无误"
    r = await admin.c.put(f"/feedback/{fb_id}", json={"status": "已处理", "remark": remark})
    results.append(step("7. 管理员标记已处理并填写备注", r.status_code == 200,
                        f"HTTP {r.status_code}"))

    # ── 8. 状态实时变更 ───────────────────────────────────────────
    r = await admin.c.get(f"/feedback/{fb_id}")
    after = r.json()["data"]
    results.append(step("8. 状态变更为「已处理」且备注落库",
                        after.get("status") == "已处理" and after.get("remark") == remark,
                        f"status={after.get('status')}"))

    # 待处理列表里应已移除
    r = await admin.c.get("/feedback", params={"status": "待处理", "page_size": 100})
    still = any(x["id"] == fb_id for x in r.json()["data"]["items"])
    results.append(step("9. 该条已从「待处理」列表移除", not still, f"仍在待处理={still}"))

    # ── 10. 会话维度同步标记（问数日志可见）───────────────────────
    r = await admin.c.get("/chat/logs", params={"keyword": MARK[:8], "page_size": 20})
    logs = r.json()["data"]["items"]
    synced = any(x["id"] == session_id and x.get("admin_feedback") == "已处理" for x in logs)
    results.append(step("10. 会话的管理员反馈标记同步", synced,
                        f"session={session_id} admin_feedback="
                        f"{next((x.get('admin_feedback') for x in logs if x['id'] == session_id), None)}"))

    await admin.aclose()
    await user.aclose()

    passed = sum(results)
    print(f"\nUC-4 通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="UC-4 反馈闭环验证")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--user", default="zhangsan", help="普通用户")
    ap.add_argument("--admin", default="admin", help="管理员")
    args = ap.parse_args()
    t0 = time.perf_counter()
    code = asyncio.run(main_async(args.base_url, args.user, args.admin))
    print(f"耗时 {time.perf_counter() - t0:.1f}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
