"""模型探针：实测候选模型在「当前 base_url」下的可用性与延迟。

为什么要实测：
    不同网关托管的模型清单不同（如阿里云百炼兼容端点 ≠ DeepSeek 官方），
    模型是否可用、是否为推理型、延迟多少，只有真跑一遍才知道。

指标：
    · 可用性（模型名是否被网关接受）
    · TTFT 首字时间（用户感知的"等待多久开始出字"）
    · 是否推理型（思维链会额外吃 token 预算、显著拉长 TTFT）
    · JSON 模式下的 SQL 生成质量（能否产出可执行 SQL）

用法：
    python -m scripts.eval.probe_models
    python -m scripts.eval.probe_models --models deepseek-v4-flash,qwen3.8-max
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import litellm

litellm.suppress_debug_info = True

DEFAULT_MODELS = [
    "deepseek-v4-pro-0813",   # 当前基线（推理型）
    "deepseek-v4-flash",
    "qwen3.8-max",
    # 常见备选，便于在两个目标都不可用时给出替代建议
    "qwen-plus",
    "qwen-max",
    "qwen-flash",
    "qwen-turbo",
]

SQL_TEST = {
    "role": "user",
    "content": (
        "你是经营数据分析引擎。只输出 JSON："
        '{"thought":"一句话","sql":"SELECT ..."}\n'
        "表 bi.fact_contract 别名 f，列：unit_code, year_income, year, risk_level；"
        "表 bi.dim_unit 别名 d，列：unit_code, unit_name，"
        "JOIN 条件 d.unit_code = f.unit_code。\n"
        "问题：2026年各经营单元收入排名，取前10。"
    ),
}

CHAT_TEST = {"role": "user", "content": "用一句话说明什么是同比。"}


async def probe(model: str, api_key: str, api_base: str, max_tokens: int) -> dict:
    out = {
        "model": model, "ok": False, "ttft": 0.0, "total": 0.0,
        "reasoning": 0, "content": 0, "is_reasoning": False,
        "sql": "", "error": "",
    }
    full = f"openai/{model}" if api_base else model

    # ── ① 流式测 TTFT 与是否推理型 ────────────────────────────────
    t0 = time.perf_counter()
    try:
        resp = await litellm.acompletion(
            model=full, api_key=api_key, api_base=api_base,
            messages=[CHAT_TEST], max_tokens=max_tokens, temperature=0.1,
            stream=True,
        )
        first_token_at = None
        async for chunk in resp:
            d = chunk.choices[0].delta if chunk.choices else None
            if not d:
                continue
            rc = getattr(d, "reasoning_content", None)
            if rc:
                out["reasoning"] += len(rc)
                if first_token_at is None:
                    first_token_at = time.perf_counter()  # 思维链也算"开始有反馈"
            if d.content:
                out["content"] += len(d.content)
                if first_token_at is None:
                    first_token_at = time.perf_counter()
        out["total"] = time.perf_counter() - t0
        out["ttft"] = (first_token_at - t0) if first_token_at else out["total"]
        out["is_reasoning"] = out["reasoning"] > 0
        out["ok"] = out["content"] > 0
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:110]}"
        return out

    # ── ② JSON 模式测 SQL 生成质量 ────────────────────────────────
    t1 = time.perf_counter()
    try:
        r = await litellm.acompletion(
            model=full, api_key=api_key, api_base=api_base,
            messages=[SQL_TEST], max_tokens=max_tokens, temperature=0.0,
            response_format={"type": "json_object"},
        )
        out["sql_seconds"] = time.perf_counter() - t1
        text = r.choices[0].message.content or ""
        try:
            payload = json.loads(text)
            out["sql"] = str(payload.get("sql", ""))[:110]
        except Exception:  # noqa: BLE001
            out["sql"] = f"(非 JSON) {text[:70]}"
    except Exception as exc:  # noqa: BLE001
        out["sql"] = f"失败：{type(exc).__name__}: {str(exc)[:70]}"
    return out


async def main_async(models: list[str]) -> int:
    sys.path.insert(0, ".")
    from app.core.config import settings

    api_key, api_base = settings.LLM_API_KEY, settings.LLM_BASE_URL
    print(f"端点：{api_base or '(默认)'}")
    print(f"探测 {len(models)} 个模型\n")

    results = []
    for m in models:
        print(f"  … {m}", end="", flush=True)
        r = await probe(m, api_key, api_base, settings.LLM_MAX_TOKENS)
        results.append(r)
        tag = "可用" if r["ok"] else "不可用"
        kind = "推理型" if r["is_reasoning"] else "直出型"
        print(f"\r  [{tag}] {m:<24} TTFT {r['ttft']:.1f}s  "
              f"总 {r['total']:.1f}s  {kind}")

    print("\n" + "=" * 96)
    print(f"{'模型':<24}{'状态':<8}{'TTFT':>8}{'总耗时':>9}{'类型':<9}{' SQL 生成'}")
    print("-" * 96)
    for r in results:
        status = "可用" if r["ok"] else "不可用"
        kind = "推理型" if r["is_reasoning"] else "直出型"
        if not r["ok"]:
            print(f"{r['model']:<24}{status:<8}{'—':>8}{'—':>9}{'—':<9} {r['error'][:44]}")
            continue
        print(f"{r['model']:<24}{status:<8}{r['ttft']:>7.1f}s{r['total']:>8.1f}s{kind:<9} {r['sql'][:52]}")
        print(f"{'':<24}{'':<8}{'':>8}{'':>9}{'':<9} 耗时 {r.get('sql_seconds', 0):.1f}s")
    print("=" * 96)

    good = [r for r in results if r["ok"]]
    if good:
        fast = sorted(good, key=lambda x: x["ttft"])
        print(f"\n可用 {len(good)}/{len(results)} 个。按 TTFT 排序：")
        for r in fast:
            print(f"  {r['ttft']:>6.1f}s  {r['model']:<24} "
                  f"{'(推理型，思维链会拉长等待)' if r['is_reasoning'] else ''}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="探测候选模型可用性与延迟")
    ap.add_argument("--models", default="", help="逗号分隔的模型名；默认探测内置清单")
    args = ap.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()] or DEFAULT_MODELS
    return asyncio.run(main_async(models))


if __name__ == "__main__":
    sys.exit(main())
