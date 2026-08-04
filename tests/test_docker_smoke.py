"""Docker Compose smoke test (SP 1.109, both markets).

Brings up the full compose stack (postgres + redis) in an isolated project,
migrates a fresh database, runs Hong Kong and United States security fetches,
and verifies the runs are reproducible (an idempotent re-run inserts no new
rows). Skipped when the Docker CLI or compose plugin is unavailable.
"""

import io
import json
import os
import shutil
import subprocess
import time
import unittest
import uuid
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harbor.cli import main

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POSTGRES_PORT = "5434"
_REDIS_PORT = "6380"


def _docker_available() -> bool:
    """Return whether the docker CLI and compose plugin are usable."""
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return probe.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


@unittest.skipUnless(_docker_available(), "Docker is not available")
class DockerComposeSmokeTests(unittest.TestCase):
    """SP 1.109: compose up, migrate, and fetch both markets reproducibly."""

    def test_compose_up_fetches_hk_and_us_reproducibly(self) -> None:
        project = f"harbor-smoke-{uuid.uuid4().hex[:8]}"
        database_url = f"postgresql+psycopg://harbor:harbor@localhost:{_POSTGRES_PORT}/harbor"
        compose_env = {
            **os.environ,
            "POSTGRES_PORT": _POSTGRES_PORT,
            "REDIS_PORT": _REDIS_PORT,
        }

        def compose(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["docker", "compose", "-p", project, *args],
                cwd=str(_PROJECT_ROOT),
                env=compose_env,
                capture_output=True,
                text=True,
                timeout=300,
            )

        try:
            up = compose("up", "-d")
            self.assertEqual(up.returncode, 0, up.stderr)
            self.assertTrue(self._wait_for_postgres(compose), "postgres did not become ready")

            self._migrate(database_url)
            self._fetch("HK", database_url)
            self._fetch("US", database_url)
            self._assert_security_count(database_url, "HK", 16)
            self._assert_security_count(database_url, "US", 16)

            rerun = self._fetch("HK", database_url)
            self.assertEqual(rerun["count"], 0)
            self._assert_security_count(database_url, "HK", 16)
        finally:
            compose("down", "-v")

    def _wait_for_postgres(self, compose: Callable[..., subprocess.CompletedProcess[str]]) -> bool:
        deadline = time.time() + 120
        while time.time() < deadline:
            result = compose("exec", "-T", "postgres", "pg_isready", "-U", "harbor", "-d", "harbor")
            if result.returncode == 0:
                return True
            time.sleep(2)
        return False

    def _migrate(self, database_url: str) -> None:
        from alembic.config import Config

        from alembic import command

        config = Config(str(_PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
        original_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        try:
            command.upgrade(config, "head")
        finally:
            if original_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = original_url

    def _fetch(self, market: str, database_url: str) -> dict[str, object]:
        environment = {
            "DATABASE_URL": database_url,
            "DATA_PROVIDER_HK": "mock",
            "DATA_PROVIDER_US": "mock",
            "LOG_LEVEL": "ERROR",
        }
        output = io.StringIO()
        with (
            patch.dict(os.environ, environment, clear=True),
            redirect_stdout(output),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main(["fetch", "securities", "--market", market])
        self.assertEqual(exit_code, 0)
        return json.loads(output.getvalue())

    def _assert_security_count(self, database_url: str, market: str, expected: int) -> None:
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url)
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT COUNT(*) FROM securities WHERE market = :market"),
                {"market": market},
            ).one()
        self.assertEqual(int(row[0]), expected)
