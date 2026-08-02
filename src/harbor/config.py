"""Typed runtime settings loaded from environment variables and `.env`."""

from enum import StrEnum

from pydantic import field_validator
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
    database_url: str
    log_level: str = "INFO"

    @field_validator("data_provider_hk", "data_provider_us")
    @classmethod
    def validate_data_provider(cls, value: str) -> str:
        """Reject blank data-provider identifiers."""
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("Data provider must be a non-empty identifier.")
        return normalized_value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Require a non-empty database URL with an explicit scheme."""
        normalized_value = value.strip()
        if "://" not in normalized_value:
            raise ValueError("DATABASE_URL must include a URI scheme, such as postgresql+psycopg://.")
        return normalized_value