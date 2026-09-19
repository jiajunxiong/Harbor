"""Dependency wiring for the read-only API (MVP 5 / SP 5.1).

Settings and the database engine live on ``app.state``, so an application can
be constructed without touching a database (useful for contract tests) and the
connection is opened lazily per request through the repository layer (SP 5.8).

Tests may replace :func:`get_read_store` with ``app.dependency_overrides`` to
exercise the routers without a database.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.engine import Connection, Engine

from harbor.api.config import ApiSettings
from harbor.api.errors import ApiError
from harbor.api.read_store import ReadStore, SqlReadStore


def get_settings(request: Request) -> ApiSettings:
    """Return the settings the application was created with (SP 5.1)."""
    settings = getattr(request.app.state, "api_settings", None)
    if not isinstance(settings, ApiSettings):
        raise ApiError(
            status_code=500,
            code="misconfigured",
            detail="The API settings are not configured on this application.",
        )
    return settings


def get_engine(request: Request) -> Engine:
    """Return the engine the application was created with (SP 5.8).

    Raises:
        ApiError: 503 when no engine is configured, so a database outage is a
            clean problem+json response rather than a stack trace (SP 5.5).
    """
    engine = getattr(request.app.state, "engine", None)
    if not isinstance(engine, Engine):
        raise ApiError(
            status_code=503,
            code="database_unavailable",
            detail="This API instance has no database connection configured.",
        )
    return engine


def get_optional_engine(request: Request) -> Engine | None:
    """Return the configured engine, or ``None`` when this instance has none.

    Used by ``/health``, which must answer even before a database is wired up.
    """
    engine = getattr(request.app.state, "engine", None)
    return engine if isinstance(engine, Engine) else None


def get_connection(engine: Engine = Depends(get_engine)) -> Iterator[Connection]:
    """Yield a read connection for the duration of one request (SP 5.8)."""
    with engine.connect() as connection:
        yield connection


def get_read_store(connection: Connection = Depends(get_connection)) -> ReadStore:
    """Return the read-only store bound to this request's connection (SP 5.8)."""
    return SqlReadStore(connection)
