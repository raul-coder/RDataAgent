"""多轮对话端到端验证（UC-2）。

在一个会话内连续追问 5 轮，逐轮校验「指代消解 / 时间切换 / 条件叠加 /
结果二次加工」是否生效。

用法：python -m scripts.eval.check_multiturn
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from typing import Any

BASE = os.getenv("EVAL_BASE", "http://127.0.0.1:8000/api/v1")

# (问题, 校验函数名)
ROUNDS = [
    ("2026年各经营单元收入排名是多少", "round1"),
    ("那北京呢", "round2"),
    ("它同比呢", "round3"),
    ("只看政企行业", "round4"),
    ("换成饼图", "round5"),
]


def _post(path: str, payload: dict, token: str = ""):
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


def login(u: str, p: str) -> str:
    with _post("/auth/login", {"username": u, "password": p}) as r:
        return json.loads(r.read())["data"]["access_token"]


def ask(token: str, question: str, session_id: int) -> dict:
    out: dict[str, Any] = {"rows": 0, "columns": [], "sql": "", "intent": "", "clarify": False,
                           "chart": "", "error": "", "rewritten": "", "latency_ms": 0}
    t0 = time.perf_counter()
    try:
        with _post("/chat/completions", {"session_id": session_id, "content": question}, token) as r:
            ev = None
            for raw in r:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("event: "):
                    ev = line[7:]
                elif line.startswith("data: ") and ev:
                    d = json.loads(line[6:])
                    if ev == "sql":
                        out["sql"] = d.get("sql", "")
                    elif ev == "table":
                        out["rows"] = d.get("total", 0)
                        out["columns"] = d.get("columns", [])
                    elif ev == "chart":
                        out["chart"] = d.get("type", "")
                    elif ev == "intent":
                        out["intent"] = d.get("intent", "")
                    elif ev == "clarify":
                        out["clarify"] = True
                    elif ev == "error":
                        out["error"] = d.get("message", "")
                    elif ev == "done":
                        out["rewritten"] = d.get("rewritten", "")
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    out["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return out


def check(round_no: int, got: dict, prev: dict | None) -> tuple[bool, str]:
    sql = got["sql"].lower()
    if round_no == 1:
        ok = got["rows"] >= 5 and "2026" in sql
        return ok, "首轮：多单元排名"
    if round_no == 2:
        # 指代消解：主体切到北京，时间仍为 2026
        ok = "北京" in got["sql"] or "北京" in got["rewritten"]
        return ok, "指代消解：主体=北京，继承 2026"
    if round_no == 3:
        ok = any(k in sql for k in ("prev_income", "同比", "lag", "2025"))
        return ok, "时间切换：同比（含去年同期）"
    if round_no == 4:
        ok = "政企" in got["sql"] or "政企" in got["rewritten"]
        return ok, "条件叠加：新增政企筛选"
    if round_no == 5:
        ok = got["intent"] == "result_ops" and got["chart"] == "pie"
        return ok, "结果二次加工：切换为饼图且不重跑 SQL"
    return False, ""


def main() -> int:
    token = login("admin", "123456")
    with _post("/chat/sessions", {"title": "多轮对话验证"}, token) as r:
        sid = json.loads(r.read())["data"]["id"]

    print(f"会话 #{sid} · UC-2 多轮对话验证\n")
    passed = 0
    prev: dict | None = None
    for i, (q, _) in enumerate(ROUNDS, start=1):
        got = ask(token, q, sid)
        ok, desc = check(i, got, prev)
        passed += 1 if ok else 0
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] Q{i} {q}")
        print(f"        预期：{desc}")
        if got["rewritten"] and got["rewritten"] != q:
            print(f"        改写：{got['rewritten']}")
        print(f"        实际：意图={got['intent']} 行数={got['rows']} 图表={got['chart']} "
              f"{got['latency_ms']}ms")
        if got["sql"]:
            print(f"        SQL：{got['sql'][:120]}")
        if got["error"]:
            print(f"        错误：{got['error'][:100]}")
        print()
        prev = got

    print("-" * 80)
    print(f"多轮通过 {passed}/{len(ROUNDS)}")
    return 0 if passed == len(ROUNDS) else 1


if __name__ == "__main__":
    sys.exit(main())
