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
        self.daily_quotes_calls: list[tuple[str, list[Mapping[str, Any]]]] = []
        self.dividends_calls: list[tuple[str, list[Mapping[str, Any]]]] = []
        self.financials_calls: list[tuple[str, list[Mapping[str, Any]]]] = []
        self.corporate_actions_calls: list[tuple[str, list[Mapping[str, Any]]]] = []

    def upsert_securities(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.calls.append((market, list(rows)))
        return len(rows)

    def upsert_daily_quotes(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.daily_quotes_calls.append((market, list(rows)))
        return len(rows)

    def upsert_dividends(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.dividends_calls.append((market, list(rows)))
        return len(rows)

    def upsert_financials(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.financials_calls.append((market, list(rows)))
        return len(rows)

    def upsert_corporate_actions(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.corporate_actions_calls.append((market, list(rows)))
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


class FetchDailyCliTests(unittest.TestCase):
    """Verify the fetch daily quotes CLI commands."""

    ENVIRONMENT = {
        "DATABASE_URL": "postgresql+psycopg://harbor:secret@localhost:5432/harbor",
        "DATA_PROVIDER_HK": "mock",
        "DATA_PROVIDER_US": "mock",
    }

    def test_fetch_daily_hk(self) -> None:
        fake_repository = RecordingRepository()
        output = io.StringIO()
        with (
            patch.dict(os.environ, self.ENVIRONMENT, clear=True),
            patch("harbor.cli.Repository", return_value=fake_repository),
            patch("harbor.cli.create_engine"),
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(
                    [
                        "fetch",
                        "daily",
                        "--market",
                        "HK",
                        "--symbol",
                        "0700.HK",
                        "--start",
                        "2026-01-05",
                        "--end",
                        "2026-01-09",
                    ]
                )

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["market"], "HK")
        self.assertEqual(summary["symbol"], "0700.HK")
        self.assertEqual(summary["provider"], "mock")
        self.assertGreaterEqual(summary["count"], 1)
        market, rows = fake_repository.daily_quotes_calls[0]
        self.assertEqual(market, "HK")
        self.assertEqual(rows[0]["symbol"], "0700.HK")

    def test_fetch_daily_us(self) -> None:
        fake_repository = RecordingRepository()
        output = io.StringIO()
        with (
            patch.dict(os.environ, self.ENVIRONMENT, clear=True),
            patch("harbor.cli.Repository", return_value=fake_repository),
            patch("harbor.cli.create_engine"),
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(
                    [
                        "fetch",
                        "daily",
                        "--market",
                        "US",
                        "--symbol",
                        "AAPL",
                        "--start",
                        "2026-01-05",
                        "--end",
                        "2026-01-09",
                    ]
                )

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["market"], "US")
        self.assertEqual(summary["symbol"], "AAPL")
        market, rows = fake_repository.daily_quotes_calls[0]
        self.assertEqual(market, "US")
        self.assertEqual(rows[0]["symbol"], "AAPL")


class FetchAllCliTests(unittest.TestCase):
    """Verify the fetch all CLI commands."""

    ENVIRONMENT = {
        "DATABASE_URL": "postgresql+psycopg://harbor:secret@localhost:5432/harbor",
        "DATA_PROVIDER_HK": "mock",
        "DATA_PROVIDER_US": "mock",
    }

    def _run(self, market: str) -> tuple[RecordingRepository, str]:
        fake_repository = RecordingRepository()
        output = io.StringIO()
        with (
            patch.dict(os.environ, self.ENVIRONMENT, clear=True),
            patch("harbor.cli.Repository", return_value=fake_repository),
            patch("harbor.cli.create_engine"),
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["fetch", "all", "--market", market])
        self.assertEqual(exit_code, 0)
        return fake_repository, output.getvalue()

    def test_fetch_all_hk(self) -> None:
        fake_repository, output = self._run("HK")
        summary = json.loads(output)
        self.assertEqual(summary["market"], "HK")
        self.assertEqual(summary["provider"], "mock")
        counts = summary["counts"]
        self.assertGreaterEqual(counts["securities"], 10)
        self.assertGreaterEqual(counts["daily_quotes"], 1)
        self.assertGreaterEqual(counts["dividends"], 1)
        self.assertGreaterEqual(counts["financials"], 1)
        self.assertGreaterEqual(counts["corporate_actions"], 1)
        self.assertEqual(summary["count"], sum(counts.values()))
        self.assertEqual(fake_repository.calls[0][0], "HK")
        self.assertEqual(fake_repository.daily_quotes_calls[0][0], "HK")
        self.assertEqual(fake_repository.dividends_calls[0][0], "HK")
        self.assertEqual(fake_repository.financials_calls[0][0], "HK")
        self.assertEqual(fake_repository.corporate_actions_calls[0][0], "HK")

    def test_fetch_all_us(self) -> None:
        fake_repository, output = self._run("US")
        summary = json.loads(output)
        self.assertEqual(summary["market"], "US")
        self.assertEqual(summary["provider"], "mock")
        counts = summary["counts"]
        self.assertGreaterEqual(counts["securities"], 10)
        self.assertGreaterEqual(counts["daily_quotes"], 1)
        self.assertGreaterEqual(counts["dividends"], 1)
        self.assertGreaterEqual(counts["financials"], 1)
        self.assertGreaterEqual(counts["corporate_actions"], 1)
        self.assertEqual(summary["count"], sum(counts.values()))
        self.assertEqual(fake_repository.calls[0][0], "US")
        self.assertEqual(fake_repository.daily_quotes_calls[0][0], "US")
        self.assertEqual(fake_repository.dividends_calls[0][0], "US")
        self.assertEqual(fake_repository.financials_calls[0][0], "US")
        self.assertEqual(fake_repository.corporate_actions_calls[0][0], "US")
