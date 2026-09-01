"""问数链路端到端基线测试（I2 验收用）。

登录 → 逐条提问 → 解析 SSE → 统计成功率与耗时。

用法：
    python -m scripts.eval.check_pipeline
    python -m scripts.eval.check_pipeline --username zhangsan   # 验证数据权限隔离
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:8000/api/v1"

QUESTIONS = [
    "2026年各经营单元收入排名",
    "北京代表处今年达成情况",
    "高风险项目有哪些",
    "各产品线收入占比",
    "2026年每月的合同金额趋势",
    "2026年各经营单元收入同比",
    "销售最多的3个产品型号",
    "商解收入达成情况",
    "完成率低于60%的预警单元",
    "2026年回款情况",
]


def _post(path: str, payload: dict, token: str = "") -> urllib.request.addinfourl:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=300)


def login(username: str, password: str) -> str:
    with _post("/auth/login", {"username": username, "password": password}) as r:
        return json.loads(r.read())["data"]["access_token"]


def ask(token: str, question: str) -> dict:
    """发起问数，解析 SSE，返回 {ok, sql, rows, columns, latency_ms, error}。"""
    out: dict[str, Any] = {"ok": False, "sql": "", "rows": 0, "columns": [],
                           "latency_ms": 0, "error": "", "degraded": False}
    t0 = time.perf_counter()
    try:
        with _post("/chat/completions", {"content": question}, token) as r:
            event = None
            for raw in r:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: ") and event:
                    d = json.loads(line[6:])
                    if event == "sql":
                        out["sql"] = d.get("sql", "")
                    elif event == "table":
                        out["rows"] = d.get("total", 0)
                        out["columns"] = d.get("columns", [])
                    elif event == "error":
                        out["error"] = d.get("message", "")
                    elif event == "done":
                        out["degraded"] = bool(d.get("degraded"))
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    out["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    out["ok"] = not out["error"] and out["rows"] > 0
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="问数链路基线测试")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="123456")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    args = parser.parse_args()

    token = login(args.username, args.password)
    questions = QUESTIONS[: args.limit] if args.limit else QUESTIONS

    print(f"账号 {args.username} · 共 {len(questions)} 条问题\n")
    print(f"{'问题':<26}{'结果':<6}{'行数':>6}{'耗时':>9}  SQL")
    print("-" * 110)

    ok = 0
    latencies: list[int] = []
    for q in questions:
        r = ask(token, q)
        latencies.append(r["latency_ms"])
        mark = "OK" if r["ok"] else "FAIL"
        if r["ok"]:
            ok += 1
        sql = r["sql"].replace("\n", " ")
        print(f"{q[:24]:<26}{mark:<6}{r['rows']:>6}{r['latency_ms']:>8}ms  {sql[:62]}")
        if r["error"]:
            print(f"{'':<26}  └─ {r['error'][:88]}")

    total = len(questions)
    print("-" * 110)
    print(f"成功率 {ok}/{total} = {ok / total * 100:.0f}%")
    if latencies:
        latencies.sort()
        print(f"耗时 P50 {latencies[total // 2] / 1000:.1f}s  "
              f"最大 {latencies[-1] / 1000:.1f}s")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
