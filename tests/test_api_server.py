"""Runtime entry-point tests (MVP 5 / SP 5.1).

`harbor.api.app` takes its settings and engine as arguments so tests can build
an application without a database; `harbor.api.server` is the module that reads
the process environment and builds the application an actual server runs. These
tests cover the wiring between the two, and they deliberately keep the read-only
route-table guarantee in view: the application a server exposes must be the same
one the contract tests pin.
"""

from __future__ import annotations

import asyncio
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from harbor.api.server import UVICORN_TARGET, build_application, serve_api
from harbor.cli import main

#: A URL that parses but is never connected to: `create_engine` is lazy, so
#: building the application must not require a reachable database.
DATABASE_URL = "postgresql+psycopg://harbor:secret@localhost:5433/harbor"

ENVIRONMENT = {
    "DATABASE_URL": DATABASE_URL,
    "HARBOR_API_TOKEN": "dev-read-token",
}

READ_ONLY_METHODS = ("GET", "HEAD", "OPTIONS")


def get(app: object, path: str, *, token: str | None = None) -> httpx.Response:
    """Send one GET to an ASGI application and return the response."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}

    async def _send() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url="http://harbor.test") as client:
            return await client.get(path, headers=headers)

    return asyncio.run(_send())


class BuildApplicationTests(unittest.TestCase):
    """The application a server runs is built from the process environment."""

    def test_builds_from_the_environment(self) -> None:
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            app = build_application()
        self.assertEqual(app.state.api_settings.auth_required, True)

    def test_the_served_application_is_still_read_only(self) -> None:
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            app = build_application()
        paths = app.openapi()["paths"]
        self.assertTrue(paths)
        for path, operations in paths.items():
            for method in operations:
                self.assertIn(
                    method.upper(),
                    READ_ONLY_METHODS,
                    f"{method.upper()} {path} would be a write route on the served application.",
                )

    def test_refuses_to_start_without_a_read_token(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": DATABASE_URL}, clear=True):
            with self.assertRaises(ValidationError) as context:
                build_application()
        self.assertIn("HARBOR_API_TOKEN", str(context.exception))

    def test_allows_an_explicitly_unauthenticated_local_instance(self) -> None:
        environment = {**ENVIRONMENT, "HARBOR_API_ALLOW_UNAUTHENTICATED": "1"}
        environment.pop("HARBOR_API_TOKEN")
        with patch.dict(os.environ, environment, clear=True):
            app = build_application()
        self.assertEqual(app.state.api_settings.auth_required, False)

    def test_the_configured_token_authenticates_requests(self) -> None:
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            app = build_application()

        # /version needs no database, so this exercises env -> token -> auth
        # end to end without a running PostgreSQL (SP 5.4).
        unauthenticated = get(app, "/api/v1/version")
        self.assertEqual(unauthenticated.status_code, 401)

        authenticated = get(app, "/api/v1/version", token="dev-read-token")
        self.assertEqual(authenticated.status_code, 200)
        self.assertTrue(authenticated.json()["read_only"])

    def test_health_answers_before_any_database_is_reachable(self) -> None:
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            app = build_application()
        response = get(app, "/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "unavailable")


class ServeApiTests(unittest.TestCase):
    """`serve_api` hands the application to uvicorn without running one here."""

    def test_forwards_the_bind_parameters_and_uses_a_factory_target(self) -> None:
        with patch("uvicorn.run") as run_mock:
            serve_api(host="0.0.0.0", port=9001, reload=True)  # noqa: S104
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(run_mock.call_args.args[0], UVICORN_TARGET)
        self.assertTrue(kwargs["factory"])
        self.assertEqual(kwargs["host"], "0.0.0.0")  # noqa: S104
        self.assertEqual(kwargs["port"], 9001)
        self.assertTrue(kwargs["reload"])

    def test_defaults_to_a_local_bind(self) -> None:
        with patch("uvicorn.run") as run_mock:
            serve_api()
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], 8000)
        self.assertFalse(kwargs["reload"])


class ApiCliWiringTests(unittest.TestCase):
    """`harbor-cli api serve` is the documented way to start the service."""

    def test_help_lists_the_serve_command(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_context:
                    main(["api", "--help"])
        self.assertEqual(exit_context.exception.code, 0)
        self.assertIn("serve", output.getvalue())

    def test_serve_forwards_the_flags(self) -> None:
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            with patch("harbor.api.server.serve_api") as serve_mock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    exit_code = main(
                        ["api", "serve", "--host", "0.0.0.0", "--port", "9001", "--reload"]
                    )
        self.assertEqual(exit_code, 0)
        kwargs = serve_mock.call_args.kwargs
        self.assertEqual(kwargs["host"], "0.0.0.0")  # noqa: S104
        self.assertEqual(kwargs["port"], 9001)
        self.assertTrue(kwargs["reload"])

    def test_serve_defaults_to_localhost(self) -> None:
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            with patch("harbor.api.server.serve_api") as serve_mock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    main(["api", "serve"])
        kwargs = serve_mock.call_args.kwargs
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], 8000)
        self.assertFalse(kwargs["reload"])

    def test_the_read_only_boundary_is_stated_before_serving(self) -> None:
        errors = io.StringIO()
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            with patch("harbor.api.server.serve_api"):
                with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                    main(["api", "serve", "--port", "9002"])
        self.assertIn("read-only", errors.getvalue())
        self.assertIn("9002", errors.getvalue())

    def test_a_missing_token_is_an_actionable_usage_error(self) -> None:
        errors = io.StringIO()
        with patch.dict(os.environ, {"DATABASE_URL": DATABASE_URL}, clear=True):
            with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                with self.assertRaises(SystemExit) as exit_context:
                    main(["api", "serve"])
        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("HARBOR_API_TOKEN", errors.getvalue())

    def test_an_occupied_port_is_an_actionable_usage_error(self) -> None:
        errors = io.StringIO()
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            with patch(
                "harbor.api.server.serve_api", side_effect=OSError("address already in use")
            ):
                with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                    with self.assertRaises(SystemExit) as exit_context:
                        main(["api", "serve"])
        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("address already in use", errors.getvalue())

    def test_an_unknown_api_subcommand_is_a_usage_error(self) -> None:
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_context:
                    main(["api", "restart"])
        self.assertEqual(exit_context.exception.code, 2)

    def test_the_command_is_advertised_in_the_top_level_help(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--help"])
        self.assertIn("api", output.getvalue())


if __name__ == "__main__":
    unittest.main()
