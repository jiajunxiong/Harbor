"""Smoke check for Harbor runtime configuration and database access."""

import sys

import psycopg
from pydantic import ValidationError

from harbor.config import Settings
from harbor.logging import configure_logging, get_logger


def main() -> int:
    """Load settings, emit structured logs, and verify PostgreSQL connectivity."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        print(f"Configuration loading failed: {error}", file=sys.stderr)
        return 1

    configure_logging(settings.log_level)
    logger = get_logger("smoke")
    logger.info(
        "configuration_loaded",
        extra={
            "market_target": settings.market_target,
            "data_provider_hk": settings.data_provider_hk,
            "data_provider_us": settings.data_provider_us,
        },
    )

    try:
        with psycopg.connect(
            _driver_connection_url(settings.database_url), connect_timeout=5
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
    except psycopg.Error:
        logger.exception("database_connection_failed")
        return 1

    logger.info("database_connection_succeeded")
    return 0


def _driver_connection_url(database_url: str) -> str:
    """Translate the SQLAlchemy psycopg scheme into a psycopg connection URI."""
    scheme, separator, remainder = database_url.partition("://")
    if scheme == "postgresql+psycopg":
        return f"postgresql://{remainder}"
    return database_url


if __name__ == "__main__":
    raise SystemExit(main())
