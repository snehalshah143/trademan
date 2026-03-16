from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Broker / Adapter ──────────────────────────────────────────────────────
    broker_adapter: str = Field(default="mock", description="'openalgo' or 'mock'")
    openalgo_host: str = Field(default="localhost:5000", description="OpenAlgo REST host:port")
    openalgo_ws_host: str = Field(default="localhost:8765", description="OpenAlgo WS host:port")
    openalgo_api_key: str = Field(default="", description="OpenAlgo API key")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite+aiosqlite:///./trademan.db",
        description="SQLAlchemy async DB URL",
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")

    # ── Market Data ───────────────────────────────────────────────────────────
    ltp_poll_interval_seconds: float = Field(default=0.5, description="LTP polling interval (mock adapter)")
    ltp_stale_threshold_seconds: float = Field(default=5.0, description="Seconds before LTP is considered stale")
    store_ticks: bool = Field(default=False, description="Archive raw LTP ticks to TimescaleDB")

    # ── MTM ───────────────────────────────────────────────────────────────────
    mtm_snapshot_interval_seconds: float = Field(default=15.0, description="MTM snapshot interval")

    # ── Warmup ───────────────────────────────────────────────────────────────
    warmup_source: str = Field(default="none", description="'none' | 'db' | 'openalgo'")

    # ── CORS / Server ────────────────────────────────────────────────────────
    cors_origins: List[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="Allowed CORS origins",
    )
    debug: bool = Field(default=True, description="Enable debug mode")
    port: int = Field(default=8000, description="Uvicorn port")

    # ── Integrations (optional) ───────────────────────────────────────────────
    telegram_bot_token: str = Field(default="", description="Telegram bot token for alerts")
    webhook_url: str = Field(default="", description="Generic webhook URL for alert delivery")


# Singleton
settings = Settings()
