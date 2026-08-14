"""Validation CLI command tests (MVP 3 / SP 3.69).

Verifies the ``harbor-cli validation run --config <path>`` surface: it creates
a DRAFT validation run by default and returns the run id and status (默认创建
草稿并返回验证运行 ID 与状态). The service is patched for the CLI surface tests
(no database required); the service itself is exercised against a real minimal
YAML config.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harbor.core.validation_domain import ValidationStatus
from harbor.services.validation import (
    ValidationCommandResult,
    ValidationServiceError,
    run_validation_from_config,
)


def _write_config(tmp: str, *, markets: str = "HK") -> str:
    """Write a minimal valid validation config file."""
    path = Path(tmp) / "validation.yaml"
    path.write_text(
        f"markets: [{markets}]\n"
        "base_currency: HKD\n"
        "split:\n"
        "  train_start: 2019-01-01\n"
        "  train_end: 2021-12-31\n"
        "  validation_start: 2022-01-01\n"
        "  validation_end: 2022-12-31\n"
        "  test_start: 2023-01-01\n"
        "  test_end: 2026-12-30\n",
        encoding="utf-8",
    )
    return str(path)


class ValidationRunCliTests(unittest.TestCase):
    """Verify the ``validation run`` command surface (SP 3.69)."""

    def test_validation_run_prints_draft_id_and_status(self) -> None:
        from harbor.cli import main

        result = ValidationCommandResult(run_id="run-abc", status=ValidationStatus.DRAFT)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(tmp)
            with (
                patch("harbor.cli.run_validation_from_config", return_value=result) as run_mock,
            ):
                with redirect_stdout(output), redirect_stderr(io.StringIO()):
                    exit_code = main(["validation", "run", "--config", config_path])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary, {"run_id": "run-abc", "status": "DRAFT"})
        self.assertEqual(run_mock.call_args.kwargs["config_path"], config_path)

    def test_validation_run_failure_is_actionable_error(self) -> None:
        from harbor.cli import main

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(tmp)
            with (
                patch(
                    "harbor.cli.run_validation_from_config",
                    side_effect=ValidationServiceError("split boundaries are reversed"),
                ),
            ):
                with self.assertRaises(SystemExit) as exit_context:
                    with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                        main(["validation", "run", "--config", config_path])

        self.assertEqual(exit_context.exception.code, 2)
        message = stderr.getvalue()
        self.assertIn("Validation run failed", message)
        self.assertIn("split boundaries are reversed", message)

    def test_validation_run_missing_file_is_actionable_error(self) -> None:
        from harbor.cli import main

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "missing.yaml")
            with (
                patch(
                    "harbor.cli.run_validation_from_config",
                    side_effect=FileNotFoundError(missing),
                ),
            ):
                with self.assertRaises(SystemExit) as exit_context:
                    with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                        main(["validation", "run", "--config", missing])

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("Validation run failed", stderr.getvalue())


class ValidationRunServiceTests(unittest.TestCase):
    """The service creates a DRAFT run from a real config (SP 3.69)."""

    def test_service_creates_draft_run_from_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(tmp)
            result = run_validation_from_config(config_path)
            self.assertTrue(result.run_id)
            self.assertEqual(result.status, ValidationStatus.DRAFT)

    def test_service_returns_distinct_run_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(tmp)
            first = run_validation_from_config(config_path)
            second = run_validation_from_config(config_path)
            self.assertNotEqual(first.run_id, second.run_id)

    def test_service_rejects_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(
                "markets: [HK]\nbase_currency: HKD\nsplit:\n"
                "  train_start: 2019-01-01\n  train_end: 2021-12-31\n"
                "  validation_start: 2022-01-01\n  validation_end: 2022-12-31\n"
                "  test_start: 2021-01-01\n  test_end: 2026-12-30\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                run_validation_from_config(str(path))

    def test_service_rejects_missing_file(self) -> None:
        with self.assertRaises((OSError, ValueError)):
            run_validation_from_config("/no/such/validation.yaml")


if __name__ == "__main__":
    unittest.main()
