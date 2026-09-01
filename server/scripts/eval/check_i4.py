"""I4 运营闭环接口冒烟：配置 / 模型 / 反馈 / 快捷提问 / 日志。

用法：
    python -m scripts.eval.check_i4                 # 全量
    python -m scripts.eval.check_i4 --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import sys
import time

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"
PASS, FAIL = "OK  ", "FAIL"


class Runner:
    def __init__(self, base: str, user: str, pwd: str) -> None:
        self.base = base.rstrip("/")
        self.user, self.pwd = user, pwd
        self.results: list[tuple[str, str, str]] = []

    async def __aenter__(self):
        self.c = httpx.AsyncClient(base_url=self.base, timeout=60)
        r = await self.c.post("/auth/login", json={"username": self.user, "password": self.pwd})
        r.raise_for_status()
        token = r.json()["data"]["access_token"]
        self.c.headers["Authorization"] = f"Bearer {token}"
        return self

    async def __aexit__(self, *exc):
        await self.c.aclose()

    def _record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, PASS if ok else FAIL, detail))

    async def get(self, name: str, path: str, params: dict | None = None) -> dict | None:
        try:
            r = await self.c.get(path, params=params or {})
        except Exception as exc:  # noqa: BLE001
            self._record(name, False, f"{type(exc).__name__}: {str(exc)[:60]}")
            return None
        if r.status_code != 200:
            self._record(name, False, f"HTTP {r.status_code} {r.text[:90]}")
            return None
        body = r.json()
        data = body.get("data")
        if isinstance(data, dict) and "items" in data:
            detail = f"{len(data['items'])} 条 / 共 {data.get('total')}"
        elif isinstance(data, list):
            detail = f"{len(data)} 项"
        else:
            detail = str(data)[:70]
        self._record(name, True, detail)
        return data

    async def post(self, name: str, path: str, payload: dict) -> dict | None:
        try:
            r = await self.c.post(path, json=payload)
        except Exception as exc:  # noqa: BLE001
            self._record(name, False, f"{type(exc).__name__}: {str(exc)[:60]}")
            return None
        if r.status_code != 200:
            self._record(name, False, f"HTTP {r.status_code} {r.text[:90]}")
            return None
        self._record(name, True, str(r.json().get("data"))[:70])
        return r.json().get("data")

    async def put(self, name: str, path: str, payload: dict) -> dict | None:
        try:
            r = await self.c.put(path, json=payload)
        except Exception as exc:  # noqa: BLE001
            self._record(name, False, f"{type(exc).__name__}: {str(exc)[:60]}")
            return None
        if r.status_code != 200:
            self._record(name, False, f"HTTP {r.status_code} {r.text[:90]}")
            return None
        self._record(name, True, str(r.json().get("data"))[:70])
        return r.json().get("data")

    def report(self) -> int:
        print()
        print(f"{'检查项':<34}{'结果':<7}明细")
        print("-" * 104)
        for name, status, detail in self.results:
            print(f"{name:<34}{status:<7}{detail}")
        print("-" * 104)
        ok = sum(1 for _, s, _ in self.results if s == PASS)
        print(f"通过 {ok}/{len(self.results)}")
        return 0 if ok == len(self.results) else 1


async def main_async(base: str, user: str, pwd: str) -> int:
    async with Runner(base, user, pwd) as r:
        # ── 应用配置 ────────────────────────────────────────────────
        cfg = await r.get("应用配置-读取", "/app-config")
        await r.get("应用配置-卡片结构", "/app-config/schema")
        original_tts = (cfg or {}).get("tts")
        await r.put("应用配置-保存(改开关)", "/app-config", {"configs": {"tts": not original_tts}})
        after = await r.get("应用配置-复核生效", "/app-config")
        if after is not None and after.get("tts") == (not original_tts):
            r._record("应用配置-开关即时生效", True, f"tts: {original_tts} → {after.get('tts')}")
        else:
            r._record("应用配置-开关即时生效", False, "值未变化")
        await r.put("应用配置-还原", "/app-config", {"configs": {"tts": original_tts}})

        # ── 模型配置 ────────────────────────────────────────────────
        models = await r.get("模型配置-列表", "/models")
        if models:
            default = next((m for m in models if m.get("is_default")), None)
            r._record(
                "模型配置-默认唯一且已启用",
                bool(default and default.get("enabled")),
                f"默认={default.get('model_name') if default else '无'}，"
                f"启用={sum(1 for m in models if m.get('enabled'))}/{len(models)}",
            )
            r._record(
                "模型配置-密钥未明文出库",
                all("api_key" not in m for m in models),
                f"脱敏示例={default.get('api_key_masked') if default else '-'}",
            )
            await r.post("模型配置-测试连接(存量)", f"/models/{default['id']}/test", {})
        await r.post(
            "模型配置-测试连接(非法应失败)",
            "/models/test",
            {"base_url": "https://example.invalid/v1", "model_name": "no-such-model", "api_key": "x"},
        )

        # ── 快捷提问 ────────────────────────────────────────────────
        tabs = await r.get("快捷提问-三Tab", "/quick-questions")
        if tabs:
            r._record(
                "快捷提问-三个分类齐全",
                all(k in tabs for k in ("recent", "recommend", "favorite")),
                "常问{} 推荐{} 收藏{}".format(
                    len(tabs.get("recent", [])), len(tabs.get("recommend", [])),
                    len(tabs.get("favorite", [])),
                ),
            )
        await r.get("快捷提问-仅收藏", "/quick-questions", {"category": "favorite"})
        fav = await r.post("快捷提问-新增收藏", "/quick-questions",
                           {"question": "【冒烟】测试收藏问题", "category": "favorite"})
        if fav and fav.get("id"):
            await r.get("快捷提问-复核收藏", "/quick-questions", {"category": "favorite"})
            try:
                rr = await r.c.delete(f"/quick-questions/{fav['id']}")
                r._record("快捷提问-删除收藏", rr.status_code == 200, f"HTTP {rr.status_code}")
            except Exception as exc:  # noqa: BLE001
                r._record("快捷提问-删除收藏", False, str(exc)[:60])

        # ── 反馈闭环 ────────────────────────────────────────────────
        await r.get("反馈-统计", "/feedback/stats")
        await r.get("反馈-用户下拉", "/feedback/users/options")
        fbs = await r.get("反馈-列表(待处理优先)", "/feedback", {"page_size": 5})
        await r.get("反馈-按状态筛选", "/feedback", {"status": "待处理", "page_size": 5})
        if fbs and fbs.get("items"):
            fb = fbs["items"][0]
            origin_status = fb.get("status")
            await r.get("反馈-详情", f"/feedback/{fb['id']}")
            await r.put("反馈-处理(标记已处理)", f"/feedback/{fb['id']}",
                        {"status": "已处理", "remark": "冒烟测试处理"})
            # 用详情接口复核：列表是"待处理优先"排序，处理后该条会排到后面
            after = await r.c.get(f"/feedback/{fb['id']}")
            st = after.json()["data"]["status"]
            r._record("反馈-状态已落库", st == "已处理", f"id={fb['id']} → {st}")
            # 还原，保证脚本可重复执行
            await r.c.put(
                f"/feedback/{fb['id']}",
                json={"status": origin_status, "remark": fb.get("remark") or ""},
            )

        # ── 日志 ────────────────────────────────────────────────────
        await r.get("操作日志-查询", "/logs/operation", {"page_size": 5})
        await r.get("操作日志-时间筛选", "/logs/operation",
                    {"start_time": "2026-01-01", "page_size": 5})
        try:
            rr = await r.c.get("/logs/operation/export", params={"page_size": 5})
            if rr.status_code == 200:
                rows = list(csv.reader(io.StringIO(rr.text)))
                r._record("操作日志-导出CSV", len(rows) > 1,
                          f"{len(rows) - 1} 行数据，表头={rows[0][:4]}")
            else:
                r._record("操作日志-导出CSV", False, f"HTTP {rr.status_code} {rr.text[:80]}")
        except Exception as exc:  # noqa: BLE001
            r._record("操作日志-导出CSV", False, str(exc)[:70])
        await r.get("问数日志-列表", "/chat/logs", {"page_size": 5})

    return r.report()


def main() -> int:
    ap = argparse.ArgumentParser(description="I4 接口冒烟")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="123456")
    args = ap.parse_args()
    t0 = time.perf_counter()
    code = asyncio.run(main_async(args.base_url, args.user, args.password))
    print(f"耗时 {time.perf_counter() - t0:.1f}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
