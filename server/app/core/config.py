"""应用配置（pydantic-settings，从环境变量 / .env 读取）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings 只把 .env 解析进 Settings 对象，**不会写入 os.environ**。
# 而部分第三方库（典型如 LiteLLM，读 LITELLM_LOCAL_MODEL_COST_MAP）是直接从
# os.environ 取配置的，光有 Settings 字段对它们无效。
# 这里额外 load 一次，让 .env 对这些库同样生效。
#
# override=False：已存在的真实环境变量优先，.env 只作兜底（生产注入变量不被覆盖）。
# 遍历顺序按优先级从高到低——先加载的会占位，与 Settings 的 env_file
# 顺序（".env", "../.env"，后者优先）保持一致。
for _env_path in (
    Path(__file__).resolve().parents[3] / ".env",  # 项目根（对应 ../.env，优先）
    Path(__file__).resolve().parents[2] / ".env",  # server/
):
    if _env_path.is_file():
        load_dotenv(_env_path, override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── 应用 ──────────────────────────────────────────────────────
    ENV: str = "dev"
    APP_NAME: str = "经管之星"
    APP_VERSION: str = "1.0.0"
    API_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    SECRET_KEY: str = "dev-secret-key-please-change-in-production"
    AES_KEY: str = "dev-aes-key-please-change-32b"

    # JWT
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_ALGORITHM: str = "HS256"

    # ── 数据库 ────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://jingguan:jingguan@localhost:5432/jingguan"
    # Agent 取数专用只读连接（安全兜底：即使 SQL 校验被绕过也无法写库）
    BI_READONLY_URL: Optional[str] = None
    SQL_ECHO: bool = False

    # ── 缓存 / 队列 ──────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── 对象存储 ──────────────────────────────────────────────────
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minio"
    MINIO_SECRET_KEY: str = "minio123456"
    MINIO_BUCKET: str = "jingguan"
    MINIO_SECURE: bool = False

    # ── 模型 ──────────────────────────────────────────────────────
    LLM_DEFAULT_PROVIDER: str = "deepseek"
    LLM_DEFAULT_MODEL: str = "deepseek-chat"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_FALLBACK_MODELS: str = "qwen2.5-72b-instruct,ollama/qwen2.5:7b"
    # 推理型模型（如 deepseek-v4-pro）的思维链会占用同一份 token 预算，
    # 预算过小会出现「推理写满、正文为空」。默认给足 8192。
    LLM_MAX_TOKENS: int = 8192
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_BASE_URL: str = ""

    # ── 问数安全 ──────────────────────────────────────────────────
    SQL_MAX_ROWS: int = 5000
    # 问数结果缓存秒数（0 = 关闭）。演示与日常运营里同一批问题会被反复问，
    # 命中后可直接跳过 LLM，把 10s 级调用降到毫秒级。
    # 缓存键包含数据权限，不会跨用户串数据（见 services/qa_cache.py）。
    QA_CACHE_TTL: int = 300
    SQL_TIMEOUT_MS: int = 15000
    RATE_LIMIT_QA: str = "10/minute"

    # ── 槽位抽取（规则优先 + LLM 兜底，见 agent/nodes/slot_llm.py）────
    # 规则层是字面匹配：快、确定、零成本，但覆盖不到同义改写 / 否定 /
    # 数值区间 / 同维度多值 / 取值歧义。这几类交由轻量模型补一次抽取。
    # 关闭后行为与改造前完全一致（纯规则），可作为降级开关。
    SLOT_LLM_ENABLED: bool = True
    # 置信度低于该值、或规则层判定存在「可疑」信号时，才触发 LLM 兜底。
    # 调高 → 更少调用、更快更省；调低 → 更多问法被覆盖。
    SLOT_LLM_THRESHOLD: float = 0.6
    # 兜底抽取在问数主链路内串行执行，必须设上限；超时静默回落规则结果。
    #
    # ⚠️ 这个阈值**强依赖模型速度，换模型后必须重新实测**（10 条样本）：
    #   · deepseek-v3.2（快）：约 3s 够用
    #   · qwen3.7-max  ：P50 15.3s / P90 18.0s / 最难用例 27~30s
    #   · qwen3.7-flash：P50 15.3s / P90 18.0s / 最难用例 ~29.7s
    # 兜底 Prompt 约 1600 token（远小于 SQL 生成的 5700），
    # 耗时主要由模型本身的首字延迟决定，不是 Prompt 长度问题。
    #
    # 两点设计权衡：
    # 1) 这是**上限而非固定开销**——正常调用该多久就多久，它只在调用真的拖长时
    #    才截断。所以放宽不会拖慢正常请求，只会放过本可以成功的慢调用。
    # 2) 最需要兜底的恰恰是最慢的用例：规则层 confidence=0.0（一点线索都没给）
    #    时模型要从全部指标里选，实测约 30s。阈值卡在 20s 等于精准砍掉
    #    兜底价值最高的那一批——实测 #88 在 20s 下超时澄清、30s 下成功取到 10 行。
    #
    # 失败是安全的（回落规则结果，与未开启时一致），只是没有增益。
    SLOT_LLM_TIMEOUT_MS: int = 30000
    SLOT_LLM_MAX_TOKENS: int = 1024

    # ── CORS ──────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # ── 数据截止日期（问数可解释链路展示用）──────────────────────
    DATA_AS_OF: str = "2026-12-31"
    DEFAULT_YEAR: int = 2026

    # ── 日志 ──────────────────────────────────────────────────────
    # 相对路径按 server/ 解析（不随启动目录漂移），默认 server/logs/。
    LOG_DIR: str = "logs"
    LOG_TO_FILE: bool = True
    # 单文件上限与保留份数：调试期默认 10MB × 5，约 50MB 封顶
    LOG_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.LLM_FALLBACK_MODELS.split(",") if m.strip()]

    @property
    def bi_readonly_dsn(self) -> str:
        """未单独配置只读连接时，回退到主连接（开发环境允许，生产必须区分）。"""
        return self.BI_READONLY_URL or self.DATABASE_URL

    @property
    def log_dir(self) -> Path:
        """日志目录（绝对）。相对路径按 server/ 解析，避免 `cd` 不同目录时日志散落。"""
        p = Path(self.LOG_DIR)
        if not p.is_absolute():
            # server/app/core/config.py → 上溯三级到 server/
            p = Path(__file__).resolve().parents[2] / p
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
