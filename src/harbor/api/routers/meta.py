"""Meta endpoints: health and version (MVP 5 / SP 5.2).

``/health`` is deliberately unauthenticated so a container orchestrator can
probe it, and it reports database readiness separately from liveness so a
degraded instance is visible instead of silently serving errors.

``/version`` sits under the versioned surface and therefore requires
authentication like every other API route (SP 5.4): the only unauthenticated
exception is the liveness probe above, and it answers just as well without the
capability document.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from harbor import __version__
from harbor.api.config import ApiSettings
from harbor.api.deps import get_optional_engine, get_settings
from harbor.api.schemas import API_NAME, API_VERSION, ApiInfo, HealthStatus
from harbor.api.security import require_readonly
from harbor.services.backtest import REPORT_FORMATS

router = APIRouter(tags=["meta"], dependencies=[Depends(require_readonly)])
health_router = APIRouter(tags=["meta"])


def _database_ok(engine: Engine | None) -> bool:
    """Return whether a trivial query succeeds against the configured engine."""
    if engine is None:
        return False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


@health_router.get(
    "/health",
    response_model=HealthStatus,
    summary="Liveness plus database readiness (unauthenticated)",
)
def read_health(engine: Engine | None = Depends(get_optional_engine)) -> HealthStatus:
    """Report process liveness and, separately, database readiness."""
    database: Literal["ok", "unavailable"] = "ok" if _database_ok(engine) else "unavailable"
    return HealthStatus(
        status="ok" if database == "ok" else "degraded",
        database=database,
        read_only=True,
        version=__version__,
    )


@router.get("/version", response_model=ApiInfo, summary="API identity and capabilities")
def read_version(settings: ApiSettings = Depends(get_settings)) -> ApiInfo:
    """Report the version and the read-only capability of this instance.

    The downloadable report formats are published here so the dashboard builds
    its download links from what this API actually renders instead of from a
    hardcoded list that could drift (SP 5.22).
    """
    return ApiInfo(
        name=API_NAME,
        version=__version__,
        api_version=API_VERSION,
        read_only=True,
        auth_required=settings.auth_required,
        report_formats=list(REPORT_FORMATS),
    )
