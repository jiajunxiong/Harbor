"""Backtest CLI command tests (MVP 2 / SP 2.67).

Verifies the ``harbor-cli backtest run --config <path>`` surface: it returns a
JSON run id and status, and surfaces config/run failures as actionable
exit-code-2 errors. The service and database are patched, so no database is
required.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harbor.core.backtest_domain import BacktestStatus
from harbor.services.backtest import BacktestCommandResult, BacktestServiceError


class BacktestRunCliTests(unittest.TestCase):
    """Verify the ``backtest run`` command surface (SP 2.67)."""

    ENVIRONMENT = {
        "DATABASE_URL": "postgresql+psycopg://harbor:secret@localhost:5432/harbor",
        "DATA_PROVIDER_HK": "mock",
        "DATA_PROVIDER_US": "mock",
    }

    def _config_path(self, tmp: str) -> str:
        path = Path(tmp) / "strategy.yaml"
        path.write_text("strategy: shareholder-return\n", encoding="utf-8")
        return str(path)

    def test_backtest_run_prints_run_id_and_status(self) -> None:
        from harbor import __version__
        from harbor.cli import main

        result = BacktestCommandResult(run_id="run-abc", status=BacktestStatus.COMPLETED)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._config_path(tmp)
            with (
                patch.dict(os.environ, self.ENVIRONMENT, clear=True),
                patch("harbor.cli.create_engine"),
                patch(
                    "harbor.cli.run_backtest_from_config",
                    return_value=result,
                ) as run_mock,
            ):
                with redirect_stdout(output), redirect_stderr(io.StringIO()):
                    exit_code = main(["backtest", "run", "--config", config_path])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary, {"run_id": "run-abc", "status": "COMPLETED"})
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["config_path"], config_path)
        self.assertEqual(kwargs["code_version"], __version__)
        self.assertIsNone(kwargs["data_cutoff"])

    def test_backtest_run_passes_code_version_and_cutoff(self) -> None:
        from harbor.cli import main

        result = BacktestCommandResult(run_id="run-x", status=BacktestStatus.FAILED)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._config_path(tmp)
            with (
                patch.dict(os.environ, self.ENVIRONMENT, clear=True),
                patch("harbor.cli.create_engine"),
                patch("harbor.cli.run_backtest_from_config", return_value=result) as run_mock,
            ):
                with redirect_stdout(output), redirect_stderr(io.StringIO()):
                    exit_code = main(
                        [
                            "backtest",
                            "run",
                            "--config",
                            config_path,
                            "--code-version",
                            "9.9.9",
                            "--data-cutoff",
                            "2024-06-30",
                        ]
                    )
        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary, {"run_id": "run-x", "status": "FAILED"})
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["code_version"], "9.9.9")
        self.assertEqual(str(kwargs["data_cutoff"]), "2024-06-30")

    def test_backtest_run_failure_is_actionable_error(self) -> None:
        from harbor.cli import main

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._config_path(tmp)
            with (
                patch.dict(os.environ, self.ENVIRONMENT, clear=True),
                patch("harbor.cli.create_engine"),
                patch(
                    "harbor.cli.run_backtest_from_config",
                    side_effect=BacktestServiceError("Cannot load backtest config: boom"),
                ),
            ):
                with self.assertRaises(SystemExit) as exit_context:
                    with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                        main(["backtest", "run", "--config", config_path])

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("Backtest run failed", stderr.getvalue())
        self.assertIn("boom", stderr.getvalue())

    def test_backtest_run_missing_config_is_usage_error(self) -> None:
        from harbor.cli import main

        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with self.assertRaises(SystemExit) as exit_context:
                main(["backtest", "run"])
        self.assertEqual(exit_context.exception.code, 2)

    def test_backtest_unknown_subcommand_is_usage_error(self) -> None:
        from harbor.cli import main

        stderr = io.StringIO()
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with self.assertRaises(SystemExit) as exit_context:
                with redirect_stderr(stderr):
                    main(["backtest", "export", "run-x"])
        self.assertEqual(exit_context.exception.code, 2)


class FakeShowResult:
    def __init__(self) -> None:
        self.to_dict_calls = 0

    def to_dict(self) -> dict:
        self.to_dict_calls += 1
        return {
            "run_id": "run-1",
            "status": "COMPLETED",
            "markets": ["US"],
            "data_range": {"start": "2024-01-02", "end": "2024-01-08", "cutoff": "2024-01-08"},
            "day_count": 2,
            "cumulative_return": 0.05,
            "metrics": {"sharpe": 1.25},
        }


class BacktestShowCliTests(unittest.TestCase):
    """Verify the ``backtest show`` command surface (SP 2.68)."""

    ENVIRONMENT = {
        "DATABASE_URL": "postgresql+psycopg://harbor:secret@localhost:5432/harbor",
        "DATA_PROVIDER_HK": "mock",
        "DATA_PROVIDER_US": "mock",
    }

    def test_backtest_show_prints_status_json(self) -> None:
        from harbor.cli import main

        fake_result = FakeShowResult()
        output = io.StringIO()
        with (
            patch.dict(os.environ, self.ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch("harbor.cli.show_backtest", return_value=fake_result) as show_mock,
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["backtest", "show", "run-1"])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["run_id"], "run-1")
        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(summary["markets"], ["US"])
        self.assertEqual(summary["day_count"], 2)
        self.assertEqual(summary["metrics"], {"sharpe": 1.25})
        show_mock.assert_called_once()
        self.assertEqual(show_mock.call_args.kwargs["run_id"], "run-1")

    def test_backtest_show_missing_run_id_is_usage_error(self) -> None:
        from harbor.cli import main

        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with self.assertRaises(SystemExit) as exit_context:
                main(["backtest", "show"])
        self.assertEqual(exit_context.exception.code, 2)

    def test_backtest_show_missing_run_is_actionable_error(self) -> None:
        from harbor.cli import main
        from harbor.services.backtest import BacktestShowError

        stderr = io.StringIO()
        with (
            patch.dict(os.environ, self.ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch(
                "harbor.cli.show_backtest",
                side_effect=BacktestShowError("No backtest run found for run id 'nope'."),
            ),
        ):
            with self.assertRaises(SystemExit) as exit_context:
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    main(["backtest", "show", "nope"])

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("Backtest show failed", stderr.getvalue())
        self.assertIn("No backtest run found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
