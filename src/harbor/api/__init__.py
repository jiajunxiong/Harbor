"""Read-only HTTP API for the Harbor monitoring dashboard (MVP 5 / SP 5.1).

The API exposes persisted research and simulation state so the dashboard can
render it without reaching into the database or re-deriving anything. It is
**read-only**: only ``GET`` routes exist, and the core layer never imports the
web framework (SP 5.1).

Build an application with :func:`create_app`.
"""

from harbor.api.app import create_app

__all__ = ["create_app"]
