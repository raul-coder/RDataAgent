"""应用配置（pydantic-settings，从环境变量 / .env 读取）。"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ── CORS ──────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # ── 数据截止日期（问数可解释链路展示用）──────────────────────
    DATA_AS_OF: str = "2026-12-31"
    DEFAULT_YEAR: int = 2026

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
