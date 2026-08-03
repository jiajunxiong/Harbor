"""Command-line interface tests."""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from harbor import __version__
from harbor.cli import main


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
