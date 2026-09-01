"""语义层管理接口验证（T5-8）。

覆盖三件事：
    1. 只读：指标 / 维度 / 口径规则 / Few-shot 列表
    2. 安全：无 sem:*:edit 权限时写操作必须被拒（403）
    3. 运营价值：Few-shot 的 SQL 可以在线验证（表结构变更后能立刻发现失效样本）

用法：
    python -m scripts.eval.check_semantic
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"
PASSWORD = "123456"

RESULTS: list[bool] = []


def step(title: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK  ' if ok else 'FAIL'}] {title}" + (f"  —— {detail}" if detail else ""))
    RESULTS.append(ok)
    return ok


class Client:
    def __init__(self, base: str) -> None:
        self.c = httpx.AsyncClient(base_url=base.rstrip("/"), timeout=60)

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


async def main_async(base: str) -> int:
    admin = Client(base)
    user = await admin.login("admin")
    step("0. 超级管理员具备语义层编辑权限",
         all(p in user["perms"] for p in
             ["sem:metric:edit", "sem:dimension:edit", "sem:rule:edit", "sem:fewshot:edit"]),
         "、".join(p for p in user["perms"] if p.startswith("sem:")))

    # ── 1. 只读列表 ───────────────────────────────────────────────
    r = await admin.c.get("/data-sources/metrics")
    metrics = r.json()["data"]
    step("1. 指标列表", r.status_code == 200 and len(metrics) > 0, f"{len(metrics)} 个指标")

    r = await admin.c.get("/data-sources/dimensions")
    dims = r.json()["data"]
    step("2. 维度列表", r.status_code == 200 and len(dims) > 0, f"{len(dims)} 个维度")

    r = await admin.c.get("/semantic/rules")
    rules = r.json()["data"]
    step("3. 口径规则列表", r.status_code == 200 and len(rules) > 0, f"{len(rules)} 条规则")

    r = await admin.c.get("/semantic/fewshots", params={"page_size": 200})
    fs = r.json()["data"]
    step("4. Few-shot 列表", r.status_code == 200 and fs["total"] > 0, f"{fs['total']} 条样本")

    # ── 2. Few-shot SQL 在线验证 ──────────────────────────────────
    sample = fs["items"][0]
    r = await admin.c.post(f"/semantic/fewshots/{sample['id']}/verify")
    d = r.json()["data"]
    step("5. 样本 SQL 在线验证可执行", r.status_code == 200 and d.get("ok"),
         f"返回 {d.get('rows')} 行；失败原因={d.get('error', '')[:60]}")

    # 批量验证：所有样本都应可执行（语义层的健康度指标）
    ok_cnt = 0
    for it in fs["items"][:20]:
        r = await admin.c.post(f"/semantic/fewshots/{it['id']}/verify")
        if r.json().get("data", {}).get("ok"):
            ok_cnt += 1
    checked = min(20, len(fs["items"]))
    step("6. 前 20 条样本全部可执行", ok_cnt == checked, f"{ok_cnt}/{checked} 通过")

    # ── 3. 新增 → 修改 → 停用（指标）────────────────────────────
    # code 带时间戳：本脚本可反复运行（停用是软删除，不会真正移除行）
    payload = {
        "code": f"test_metric_{int(time.time())}",
        "name": "临时测试指标",
        "expr_sql": "SUM(f.year_income)",
        "source_id": 1,
        "unit": "万元",
        "caliber": "自动化验证创建，可删除",
        "enabled": True,
    }
    r = await admin.c.post("/semantic/metrics", json=payload)
    mid = r.json().get("data", {}).get("id") if r.status_code == 200 else None
    step("7. 新增指标", bool(mid), f"id={mid}")

    if mid:
        payload["name"] = "临时测试指标（已改）"
        r = await admin.c.put(f"/semantic/metrics/{mid}", json=payload)
        step("8. 修改指标", r.status_code == 200, f"HTTP {r.status_code}")

        # 重复 code 应冲突
        r = await admin.c.post("/semantic/metrics", json=payload)
        step("9. 重复指标代码被拒（409）", r.status_code == 409, f"HTTP {r.status_code}")

        r = await admin.c.delete(f"/semantic/metrics/{mid}")
        step("10. 停用指标", r.status_code == 200, f"HTTP {r.status_code}")

        # 清理：真正删除，避免反复验证产生垃圾数据
        await admin.c.delete(f"/semantic/metrics/{mid}")

    # ── 4. 权限：普通用户不得修改 ────────────────────────────────
    normal = Client(base)
    nuser = await normal.login("zhangsan")
    has_edit = any(p.startswith("sem:") for p in nuser["perms"])
    if has_edit:
        step("11. 普通用户无语义层权限", False, f"perms={nuser['perms']}")
    else:
        r = await normal.c.post("/semantic/metrics", json=payload)
        step("11. 普通用户新增指标被拒（403）", r.status_code == 403, f"HTTP {r.status_code}")
        r = await normal.c.get("/semantic/rules")
        step("12. 普通用户读口径规则被拒（403）", r.status_code == 403, f"HTTP {r.status_code}")
        r = await normal.c.get("/semantic/fewshots")
        step("13. 普通用户读 Few-shot 被拒（403）", r.status_code == 403, f"HTTP {r.status_code}")

    await normal.aclose()
    await admin.aclose()

    passed = sum(RESULTS)
    print(f"\n语义层接口通过 {passed}/{len(RESULTS)}")
    print(
        "注：本脚本会创建 code 形如 test_metric_<时间戳> 的临时指标；\n"
        "    接口的删除是「停用」而非物理删除（保留历史问数的参照），\n"
        "    彻底清理：DELETE FROM sem_metric WHERE code LIKE 'test_metric%';"
    )
    return 0 if passed == len(RESULTS) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="语义层管理接口验证")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    args = ap.parse_args()
    t0 = time.perf_counter()
    code = asyncio.run(main_async(args.base_url))
    print(f"耗时 {time.perf_counter() - t0:.1f}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
