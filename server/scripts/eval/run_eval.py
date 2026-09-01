"""Text2SQL 准确率评测（I3）。

评测方式不是「比对 SQL 字符串」，而是**执行结果校验 + 意图校验**：
    · 有 expect_intent / expect_clarify → 校验意图或澄清行为
    · 其余 → 执行 SQL 并检查行数区间与 SQL 关键片段

用法：
    python -m scripts.eval.run_eval                    # 跑全部 100 条（较慢）
    python -m scripts.eval.run_eval --limit 20         # 抽样
    python -m scripts.eval.run_eval --category ranking
    python -m scripts.eval.run_eval --username zhangsan # 验证数据权限下的评测
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Any

BASE = os.getenv("EVAL_BASE", "http://127.0.0.1:8000/api/v1")
CASES_PATH = os.path.join(os.path.dirname(__file__), "cases.json")


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


def login(username: str, password: str) -> str:
    with _post("/auth/login", {"username": username, "password": password}) as r:
        return json.loads(r.read())["data"]["access_token"]


def ask(token: str, question: str, session_id: int = 0, creds: tuple = ()) -> dict:
    out: dict[str, Any] = {"ok": False, "rows": 0, "columns": [], "sql": "", "intent": "",
                           "clarify": False, "error": "", "latency_ms": 0, "degraded": False}
    t0 = time.perf_counter()
    try:
        try:
            resp = _post("/chat/completions",
                         {"content": question, **({"session_id": session_id} if session_id else {})},
                         token)
        except urllib.error.HTTPError as exc:
            # access token 默认 30 分钟过期，长评测需要重新登录后重试
            if exc.code == 401 and creds:
                token = login(*creds)
                resp = _post("/chat/completions",
                             {"content": question,
                              **({"session_id": session_id} if session_id else {})},
                             token)
            else:
                raise
        with resp as r:
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
                    elif event == "intent":
                        out["intent"] = d.get("intent", "")
                    elif event == "clarify":
                        out["clarify"] = True
                    elif event == "error":
                        out["error"] = d.get("message", "")
                    elif event == "done":
                        out["degraded"] = bool(d.get("degraded"))
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    out["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return out


def check(case: dict, got: dict) -> tuple[bool, str]:
    """校验单条用例。"""
    expect_intent = case.get("expect_intent")
    if expect_intent:
        return (got["intent"] == expect_intent, f"意图 {got['intent']} != {expect_intent}")

    if case.get("expect_clarify"):
        return (got["clarify"], "未触发澄清反问")

    if got["error"]:
        return False, got["error"][:70]
    if got["rows"] <= 0:
        return False, "返回 0 行"

    min_rows = case.get("min_rows")
    max_rows = case.get("max_rows")
    if min_rows is not None and got["rows"] < min_rows:
        return False, f"行数 {got['rows']} < {min_rows}"
    if max_rows is not None and got["rows"] > max_rows:
        return False, f"行数 {got['rows']} > {max_rows}"

    sql_any = case.get("sql_any") or []
    if sql_any and not any(k.lower() in got["sql"].lower() for k in sql_any):
        return False, f"SQL 缺少关键字 {sql_any}"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Text2SQL 准确率评测")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="123456")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--category", default="")
    ap.add_argument("--output", default="", help="结果 JSON 输出路径")
    args = ap.parse_args()

    cases = json.load(open(CASES_PATH, encoding="utf-8"))
    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
    if args.limit:
        cases = cases[: args.limit]

    creds = (args.username, args.password)
    token = login(*creds)
    print(f"账号 {args.username} · 共 {len(cases)} 条用例\n")

    by_cat: dict[str, list[int]] = {}
    failures: list[dict] = []
    latencies: list[int] = []

    for c in cases:
        got = ask(token, c["question"], creds=creds)
        latencies.append(got["latency_ms"])
        ok, msg = check(c, got)
        cat = c.get("category", "-")
        by_cat.setdefault(cat, [0, 0])
        by_cat[cat][1] += 1
        if ok:
            by_cat[cat][0] += 1
        else:
            failures.append({**c, "error": msg, "sql": got["sql"]})
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] #{c['id']:<3} {cat:<14} {c['question'][:26]:<28} "
              f"{got['rows']:>5}行 {got['latency_ms']:>7}ms {msg[:34]}")

    total = len(cases)
    passed = sum(v[0] for v in by_cat.values())
    print("\n" + "-" * 96)
    print("分类准确率：")
    for cat, (ok, n) in sorted(by_cat.items()):
        bar = "█" * int(ok / n * 20) + "░" * (20 - int(ok / n * 20))
        print(f"  {cat:<16} {ok:>3}/{n:<3} {bar} {ok / n * 100:>5.0f}%")
    print("-" * 96)
    print(f"总成功率 {passed}/{total} = {passed / total * 100:.0f}%")
    if latencies:
        s = sorted(latencies)
        print(f"耗时 P50 {s[len(s) // 2] / 1000:.1f}s  P90 {s[int(len(s) * 0.9)] / 1000:.1f}s  最大 {s[-1] / 1000:.1f}s")

    if failures:
        print(f"\n失败明细（{len(failures)} 条）：")
        for f in failures[:20]:
            print(f"  #{f['id']} {f['question']} -> {f['error']}")

    if args.output:
        # 评测跑完要 30 分钟，倒在"目录不存在"上太冤；这里自动创建
        out_dir = os.path.dirname(args.output)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        json.dump({"total": total, "passed": passed, "by_category": by_cat, "failures": failures},
                  open(args.output, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.output}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
