"""I4 运营闭环种子同步：模型配置 / 快捷提问 / 应用配置。

为什么需要它：
    造数阶段往 sys_model 写入了 7 个 Demo 遗留模型（GPT-4o、Claude 等），
    它们**没有 API Key**。而 Agent 链路的降级链取自「启用中的模型」，
    若直接启用，问数会先逐个去调这些无 Key 的模型并超时，把一次问数拖到分钟级。
    因此这里做两件事：
      1. 把所有无 Key 的模型置为禁用（保留在配置页可见，但不进入降级链）；
      2. 把 .env 中真实可用的模型导入并设为默认。

用法：
    python -m scripts.seed_i4
    python -m scripts.seed_i4 --dry-run    # 只打印将执行的动作
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_i4")


async def run(dry_run: bool) -> int:
    sys.path.insert(0, ".")
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.core.crypto import decrypt_secret, encrypt_secret
    from app.models import SysModel
    from app.services import config_service
    from app.services.quick_question_service import seed_recommend

    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        # ── 1. 禁用无 Key 的模型 ────────────────────────────────────
        rows = (await db.execute(select(SysModel))).scalars().all()
        keyless = [r for r in rows if not decrypt_secret(r.api_key_enc or "")]
        if keyless:
            log.info("禁用 %d 个无 API Key 的模型（避免降级链逐个超时）：", len(keyless))
            for r in keyless:
                log.info("    - %s (%s)", r.name, r.model_name)
                if not dry_run:
                    r.enabled = False
                    r.is_default = False
        else:
            log.info("无 Key 模型：0 个，无需处理")

        # ── 2. 导入 .env 的真实模型 ────────────────────────────────
        if settings.LLM_API_KEY:
            existing = (
                await db.execute(
                    select(SysModel).where(SysModel.model_name == settings.LLM_DEFAULT_MODEL)
                )
            ).scalar_one_or_none()
            if existing:
                log.info("更新已存在的模型配置：%s", settings.LLM_DEFAULT_MODEL)
                if not dry_run:
                    existing.base_url = settings.LLM_BASE_URL
                    existing.api_key_enc = encrypt_secret(settings.LLM_API_KEY)
                    existing.enabled = True
                    existing.is_default = True
            else:
                log.info("导入 .env 模型：%s → %s", settings.LLM_DEFAULT_MODEL, settings.LLM_BASE_URL)
                if not dry_run:
                    db.add(
                        SysModel(
                            name=settings.LLM_DEFAULT_MODEL,
                            provider=settings.LLM_DEFAULT_PROVIDER or "openai",
                            base_url=settings.LLM_BASE_URL,
                            model_name=settings.LLM_DEFAULT_MODEL,
                            api_key_enc=encrypt_secret(settings.LLM_API_KEY),
                            scene="chat_qa",
                            is_default=True,
                            enabled=True,
                        )
                    )
            if not dry_run:
                # 保证默认唯一
                others = (
                    await db.execute(
                        select(SysModel).where(
                            SysModel.model_name != settings.LLM_DEFAULT_MODEL,
                            SysModel.is_default.is_(True),
                        )
                    )
                ).scalars().all()
                for o in others:
                    o.is_default = False
        else:
            log.warning("LLM_API_KEY 未配置，跳过模型导入（问数将回退到 .env 直连）")

        # ── 3. 快捷提问推荐项 ───────────────────────────────────────
        if dry_run:
            log.info("将同步推荐快捷提问（幂等）")
        else:
            created = await seed_recommend(db)
            log.info("推荐快捷提问：新增 %d 条", created)

        # ── 4. 应用配置补齐默认值 ───────────────────────────────────
        if dry_run:
            log.info("将补齐缺失的应用配置项")
        else:
            created = await config_service.ensure_seeded(db)
            log.info("应用配置：补齐 %d 项", created)

        if not dry_run:
            await db.commit()

        # ── 结果回显 ────────────────────────────────────────────────
        rows = (await db.execute(select(SysModel).order_by(SysModel.id))).scalars().all()
        log.info("\n当前模型配置：")
        for r in rows:
            flag = "默认" if r.is_default else ("启用" if r.enabled else "禁用")
            has_key = "有Key" if decrypt_secret(r.api_key_enc or "") else "无Key"
            log.info("    [%s][%s] %-22s %s", flag, has_key, r.model_name, r.base_url)

    await engine.dispose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="I4 运营闭环种子同步")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的动作，不写库")
    args = ap.parse_args()
    if args.dry_run:
        log.info("=== DRY RUN ===\n")
    return asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
