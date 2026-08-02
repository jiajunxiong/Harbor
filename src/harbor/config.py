"""Typed runtime settings loaded from environment variables and `.env`."""

from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class MarketTarget(StrEnum):
    """Markets that an ingestion run may target."""

    HK = "HK"
    US = "US"
    BOTH = "BOTH"


class Settings(BaseSettings):
    """Application settings supplied through environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    market_target: MarketTarget = MarketTarget.BOTH
    data_provider_hk: str = "mock"
    data_provider_us: str = "mock"
    database_url: str = "postgresql+psycopg://harbor:harbor@localhost:5432/harbor"
    log_level: str = "INFO"