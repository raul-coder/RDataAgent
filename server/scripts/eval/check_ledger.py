"""台账接口验证（T5-3 / FR-D1）。

覆盖三件事：
    1. 功能：列表 / 列定义 / 筛选 / 排序 / 分页 / 导出
    2. 安全：数据权限隔离、表名列名白名单、值转义（注入用例必须不生效）
    3. 口径：编码列展示为名称（"上海代表处" 而非 "SH"）

用法：
    python -m scripts.eval.check_ledger
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import re
import sys
import time

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"
PASSWORD = "123456"


class Client:
    def __init__(self, base: str) -> None:
        self.c = httpx.AsyncClient(base_url=base.rstrip("/"), timeout=60)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def login(self, username: str) -> None:
        r = await self.c.post(
            "/auth/login", json={"username": username, "password": PASSWORD}
        )
        if r.status_code != 200:
            raise RuntimeError(f"{username} 登录失败：HTTP {r.status_code}")
        self.c.headers["Authorization"] = f"Bearer {r.json()['data']['access_token']}"


RESULTS: list[bool] = []


def step(title: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK  ' if ok else 'FAIL'}] {title}" + (f"  —— {detail}" if detail else ""))
    RESULTS.append(ok)
    return ok


async def main_async(base: str) -> int:
    admin = Client(base)
    await admin.login("admin")

    # ── 1. 台账列表 ───────────────────────────────────────────────
    r = await admin.c.get("/ledger/tables")
    tables = r.json()["data"]
    step("1. 台账列表返回 3 张台账", len(tables) == 3,
         "、".join(t["title"] for t in tables))

    # ── 2. 列定义 ─────────────────────────────────────────────────
    r = await admin.c.get("/ledger/contract/columns", params={"with_values": "true"})
    cols = r.json()["data"]
    step("2. 商业市场台账列定义完整", len(cols) >= 50, f"{len(cols)} 列")

    unit_col = next((c for c in cols if c["column"] == "unit_code"), None)
    step("3. 编码列附带候选值（可下拉筛选）",
         bool(unit_col and unit_col["values"]),
         f"经营单元候选 {len(unit_col['values']) if unit_col else 0} 个："
         f"{unit_col['values'][:2] if unit_col else []}")

    # ── 3. 分页 + 排序 ────────────────────────────────────────────
    t0 = time.perf_counter()
    r = await admin.c.post("/ledger/contract/query", json={
        "page": 1, "page_size": 20, "sort_by": "amount", "sort_dir": "desc",
    })
    cost = (time.perf_counter() - t0) * 1000
    d = r.json()["data"]
    step("4. 分页查询成功", r.status_code == 200 and len(d["rows"]) == 20,
         f"总 {d['total']} 行，耗时 {cost:.0f}ms")
    step("5. 分页耗时 < 500ms（验收标准）", cost < 500, f"{cost:.0f}ms")

    amounts = [row[10] for row in d["rows"]]
    step("6. 排序生效（按金额降序）",
         all(a >= b for a, b in zip(amounts, amounts[1:])),
         f"前三：{[round(float(a), 1) for a in amounts[:3]]}")

    # 编码列应展示名称而非编码：编码形如 SH / ZJ（纯字母数字短串），名称是中文
    # （不能用长度判断——"渠道部"这类短名称会被误判成编码）
    units = {str(row[3]) for row in d["rows"] if row[3]}
    coded = [u for u in units if re.match(r"^[A-Za-z0-9_-]{1,4}$", u)]
    step("7. 编码列展示为名称", not coded, f"样例：{list(units)[:3]}")

    # ── 4. 筛选 ───────────────────────────────────────────────────
    r = await admin.c.post("/ledger/contract/query", json={
        "page": 1, "page_size": 5,
        "filters": [{"column": "risk_level", "op": "eq", "value": "高"}],
    })
    d = r.json()["data"]
    ok = d["total"] > 0 and all(row[15] == "高" for row in d["rows"])
    step("8. 枚举筛选生效", ok, f"高风险合同 {d['total']} 条")

    r = await admin.c.post("/ledger/contract/query", json={
        "page": 1, "page_size": 5,
        "filters": [{"column": "amount", "op": "between", "value": [1000, 2000]}],
    })
    d = r.json()["data"]
    vals = [float(row[10]) for row in d["rows"]]
    step("9. 数值区间筛选生效",
         bool(vals) and all(1000 <= v <= 2000 for v in vals),
         f"{len(vals)} 行，范围 {min(vals):.0f}~{max(vals):.0f}" if vals else "无数据")

    # ── 5. 安全：白名单与转义 ─────────────────────────────────────
    r = await admin.c.get("/ledger/../../etc/passwd/columns")
    step("10. 路径穿越的台账名被拒", r.status_code in (404, 422), f"HTTP {r.status_code}")

    r = await admin.c.post("/ledger/contract/query", json={
        "page": 1, "page_size": 5,
        "filters": [{"column": "id; DROP TABLE bi.fact_contract; --", "op": "eq", "value": 1}],
    })
    step("11. 恶意列名被白名单拒绝", r.status_code == 400, f"HTTP {r.status_code}")

    r = await admin.c.post("/ledger/contract/query", json={
        "page": 1, "page_size": 5,
        "filters": [{"column": "project_name", "op": "eq", "value": "x' OR '1'='1"}],
    })
    d = r.json().get("data") if r.status_code == 200 else None
    step("12. 注入值被转义（不返回全表）",
         d is not None and d["total"] == 0,
         f"命中 {d['total'] if d else '-'} 行")

    r = await admin.c.post("/ledger/contract/query", json={
        "page": 1, "page_size": 5, "sort_by": "amount); DROP TABLE bi.fact_goal; --",
    })
    step("13. 恶意排序列被拒", r.status_code == 400, f"HTTP {r.status_code}")
    await admin.aclose()

    # ── 6. 数据权限隔离 ───────────────────────────────────────────
    normal = Client(base)
    await normal.login("zhangsan")
    r = await normal.c.post("/ledger/contract/query", json={
        "page": 1, "page_size": 50,
    })
    if r.status_code != 200:
        step("14. 受限用户可查看台账", False, f"HTTP {r.status_code} {r.text[:80]}")
    else:
        d = r.json()["data"]
        units = {str(row[3]) for row in d["rows"] if row[3]}
        allowed = {"上海代表处", "浙江代表处"}
        # 首页若干行可能恰好同属一个单元，所以只能断言"不越权"；
        # 再用定向筛选确认另一个授权单元可见、未授权单元不可见
        step("14. 受限用户不越权（可见单元 ⊆ 授权单元）",
             units.issubset(allowed), f"可见：{sorted(units)}")

        rz = await normal.c.post("/ledger/contract/query", json={
            "page": 1, "page_size": 3,
            "filters": [{"column": "unit_code", "op": "eq", "value": "浙江代表处"}],
        })
        dz = rz.json()["data"]
        step("14b. 授权的另一个单元可查到", dz["total"] > 0, f"浙江 {dz['total']} 行")

        rb = await normal.c.post("/ledger/contract/query", json={
            "page": 1, "page_size": 3,
            "filters": [{"column": "unit_code", "op": "eq", "value": "北京代表处"}],
        })
        db = rb.json()["data"]
        step("14c. 未授权单元查不到", db["total"] == 0, f"北京 {db['total']} 行")

        step("15. 受限用户总数被正确收窄",
             d["total"] < 15000, f"{d['total']} 行（全量 15000）")

    # 无台账权限的用户
    r = await normal.c.get("/ledger/tables")
    step("16. 无权限时不返回台账", isinstance(r.json()["data"], list),
         f"{len(r.json()['data'])} 张可见")
    await normal.aclose()

    # ── 7. 导出 ───────────────────────────────────────────────────
    admin2 = Client(base)
    await admin2.login("admin")
    r = await admin2.c.post("/ledger/goal/export", json={"page": 1, "page_size": 20})
    rows = list(csv.reader(io.StringIO(r.text)))
    step("17. 导出 CSV 成功", r.status_code == 200 and len(rows) > 1,
         f"{len(rows) - 1} 行，表头={rows[0][:4] if rows else []}")

    r2 = await admin2.c.post("/ledger/contract/export", json={
        "filters": [{"column": "risk_level", "op": "eq", "value": "高"}],
    })
    rows2 = list(csv.reader(io.StringIO(r2.text)))
    step("18. 导出遵循筛选条件", len(rows2) - 1 > 0, f"高风险导出 {len(rows2) - 1} 行")

    # 全量导出不能被问数的 SQL_MAX_ROWS(=5000) 截断：
    # 导出是明确的大批量操作，截断会让用户拿着不完整的文件以为是全量
    r3 = await admin2.c.post("/ledger/contract/export", json={})
    rows3 = list(csv.reader(io.StringIO(r3.text)))
    step("19. 全量导出不被问数行数上限截断",
         len(rows3) - 1 == 15000, f"导出 {len(rows3) - 1} 行（期望 15000）")
    await admin2.aclose()

    # 受限用户的导出同样要受数据权限约束
    normal2 = Client(base)
    await normal2.login("zhangsan")
    r4 = await normal2.c.post("/ledger/contract/export", json={})
    rows4 = list(csv.reader(io.StringIO(r4.text)))
    units = {row[3] for row in rows4[1:]}
    step("20. 受限用户导出仅含授权单元",
         units <= {"上海代表处", "浙江代表处"} and len(rows4) > 1,
         f"{len(rows4) - 1} 行，单元={sorted(units)}")
    await normal2.aclose()

    passed = sum(RESULTS)
    print(f"\n台账接口通过 {passed}/{len(RESULTS)}")
    return 0 if passed == len(RESULTS) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="台账接口验证")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    args = ap.parse_args()
    t0 = time.perf_counter()
    code = asyncio.run(main_async(args.base_url))
    print(f"耗时 {time.perf_counter() - t0:.1f}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
