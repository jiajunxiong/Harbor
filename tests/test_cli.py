"""Command-line interface tests."""

import io
import json
import os
import unittest
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from typing import Any
from unittest.mock import patch

from harbor import __version__
from harbor.cli import main


class RecordingRepository:
    """A lightweight in-memory stand-in for the storage repository."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Mapping[str, Any]]]] = []

    def upsert_securities(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.calls.append((market, list(rows)))
        return len(rows)

    def record_raw_payload(
        self,
        market: str,
        run_id: str,
        endpoint: str,
        payload: Mapping[str, object],
        retrieved_at: datetime,
        symbol: str | None = None,
    ) -> int:
        return 1


class CliTests(unittest.TestCase):
    """Verify Harbor's public command-line behavior."""

    def test_version_prints_package_version(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_context:
                main(["--version"])

        self.assertEqual(exit_context.exception.code, 0)
        self.assertEqual(output.getvalue(), f"{__version__}\n")

    def test_config_renders_non_secret_settings(self) -> None:
        environment = {
            "DATABASE_URL": "postgresql+psycopg://harbor:secret@localhost:5432/harbor",
            "MARKET_TARGET": "US",
            "DATA_PROVIDER_HK": "akshare",
            "DATA_PROVIDER_US": "yfinance",
            "LOG_LEVEL": "debug",
        }
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=True):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["config"])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["market_target"], "US")
        self.assertEqual(summary["data_provider_hk"], "akshare")
        self.assertEqual(summary["data_provider_us"], "yfinance")
        self.assertEqual(summary["log_level"], "DEBUG")
        self.assertNotIn("database_url", summary)

    def test_providers_prints_capability_report(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["providers"])

        self.assertEqual(exit_code, 0)
        self.assertIn("mock", output.getvalue())
        self.assertIn("yfinance", output.getvalue())


class FetchSecuritiesCliTests(unittest.TestCase):
    """Verify the fetch securities CLI commands."""

    ENVIRONMENT = {
        "DATABASE_URL": "postgresql+psycopg://harbor:secret@localhost:5432/harbor",
        "DATA_PROVIDER_HK": "mock",
        "DATA_PROVIDER_US": "mock",
    }

    def test_fetch_securities_hk(self) -> None:
        fake_repository = RecordingRepository()
        output = io.StringIO()
        with (
            patch.dict(os.environ, self.ENVIRONMENT, clear=True),
            patch("harbor.cli.Repository", return_value=fake_repository),
            patch("harbor.cli.create_engine"),
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["fetch", "securities", "--market", "HK"])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["market"], "HK")
        self.assertEqual(summary["provider"], "mock")
        self.assertGreaterEqual(summary["count"], 10)
        self.assertEqual(fake_repository.calls[0][0], "HK")

    def test_fetch_securities_us(self) -> None:
        fake_repository = RecordingRepository()
        output = io.StringIO()
        with (
            patch.dict(os.environ, self.ENVIRONMENT, clear=True),
            patch("harbor.cli.Repository", return_value=fake_repository),
            patch("harbor.cli.create_engine"),
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["fetch", "securities", "--market", "US"])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["market"], "US")
        self.assertEqual(fake_repository.calls[0][0], "US")

    def test_fetch_securities_rejects_unknown_market(self) -> None:
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with self.assertRaises(SystemExit) as exit_context:
                main(["fetch", "securities", "--market", "EU"])
        self.assertEqual(exit_context.exception.code, 2)

    def test_fetch_securities_rejects_both_market(self) -> None:
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with self.assertRaises(SystemExit) as exit_context:
                main(["fetch", "securities", "--market", "BOTH"])
        self.assertEqual(exit_context.exception.code, 2)
