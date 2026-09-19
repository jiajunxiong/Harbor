"""Runtime wiring for the read-only API (MVP 5 / SP 5.1).

:mod:`harbor.api.app` deliberately takes its settings and engine as arguments,
so an application can be built without a database and the contract tests can
drive it (SP 5.11). This module is the opposite end of that design: it reads the
process environment and builds the application an actual server runs, which is
what ``uvicorn`` and ``harbor-cli api serve`` wrap.

Nothing here widens the read-only boundary. The engine reaches only the read
store (SP 5.8) and the route table is still built by :func:`create_app`, so the
route-table guarantee asserted in ``tests/test_api_contract.py`` holds for the
served application too (SP 5.3). The process exposes only a read token; there is
no setting that enables a write path (SP 5.4).
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import create_engine

from harbor.api.app import create_app
from harbor.api.config import ApiSettings
from harbor.config import Settings

#: Import string rather than an object so ``--reload`` and worker processes can
#: import it themselves; uvicorn requires a string in those modes (SP 5.1).
UVICORN_TARGET = "harbor.api.server:build_application"


def build_application() -> FastAPI:
    """Build the API from the process environment (uvicorn factory target).

    Reads ``HARBOR_API_*`` through :class:`~harbor.api.config.ApiSettings` and
    the database URL through :class:`~harbor.config.Settings`.

    Creating the engine does **not** open a connection, so an unreachable
    database surfaces later as a visible ``503`` on data routes and a
    ``degraded`` health response instead of an import-time crash (SP 5.8).

    Raises:
        pydantic.ValidationError: If no read token is configured and
            unauthenticated use was not explicitly enabled (SP 5.4).
    """
    api_settings = ApiSettings()
    app_settings = Settings()  # type: ignore[call-arg]
    return create_app(api_settings, engine=create_engine(app_settings.database_url))


def serve_api(*, host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the read-only API with uvicorn (blocks until interrupted).

    The engine is process-scoped and is not disposed explicitly, matching the
    other CLI entry points in this repository; uvicorn finishes in-flight
    requests before exiting and the OS reclaims the sockets.
    """
    import uvicorn

    uvicorn.run(
        UVICORN_TARGET,
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
