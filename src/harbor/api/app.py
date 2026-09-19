"""Application factory for the read-only API (MVP 5 / SP 5.1).

The factory takes its settings and engine explicitly so an application can be
built without a database (contract tests) and so the CLI owns connection
configuration. Only ``GET`` routes are registered — the read-only guarantee is
a property of the route table, and a test asserts it (SP 5.3).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import Engine

from harbor import __version__
from harbor.api.config import ApiSettings
from harbor.api.errors import register_error_handlers, request_id_middleware
from harbor.api.routers import backtest, meta, paper, quality, validation
from harbor.api.schemas import API_VERSION

READ_ONLY_METHODS = ("GET", "HEAD", "OPTIONS")

_logger = logging.getLogger("harbor.api")

_DESCRIPTION = (
    "Read-only access to persisted backtest, validation and paper-trading state. "
    "The API never places an order, never mutates a research artifact and never "
    "returns credentials."
)


def create_app(settings: ApiSettings, *, engine: Engine | None = None) -> FastAPI:
    """Build the read-only monitoring API (SP 5.1).

    Args:
        settings: Validated API settings (auth, CORS, pagination bounds).
        engine: Database engine used by the read store; ``None`` builds an
            application whose data routes report 503 instead of failing at
            import time.
    """
    app = FastAPI(
        title="Harbor monitoring API",
        version=__version__,
        description=_DESCRIPTION,
        openapi_url=f"/api/{API_VERSION}/openapi.json",
        docs_url=f"/api/{API_VERSION}/docs",
        redoc_url=None,
    )
    app.state.api_settings = settings
    app.state.engine = engine

    if settings.allow_unauthenticated:
        _logger.warning(
            "HARBOR_API_ALLOW_UNAUTHENTICATED is enabled: every request is accepted "
            "with the read-only role."
        )

    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=list(READ_ONLY_METHODS),
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            allow_credentials=False,
        )

    prefix = f"/api/{API_VERSION}"
    app.include_router(meta.health_router)
    app.include_router(meta.router, prefix=prefix)
    app.include_router(backtest.router, prefix=prefix)
    app.include_router(validation.router, prefix=prefix)
    app.include_router(paper.router, prefix=prefix)
    app.include_router(quality.router, prefix=prefix)
    return app
