"""Validation CLI command tests (MVP 3 / SP 3.69).

Verifies the ``harbor-cli validation run --config <path>`` surface: it creates
a DRAFT validation run by default and returns the run id and status (默认创建
草稿并返回验证运行 ID 与状态). The service is patched for the CLI surface tests
(no database required); the service itself is exercised against a real minimal
YAML config.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from harbor.core.validation_domain import ValidationStatus
from harbor.services.validation import (
    ValidationCommandResult,
    ValidationReportError,
    ValidationServiceError,
    ValidationShowError,
    ValidationShowResult,
    _render_report,
    _report_artifact_from_rows,
    _show_from_rows,
    advance_validation,
    run_validation_from_config,
)

ENVIRONMENT = {
    "DATABASE_URL": "postgresql+psycopg://harbor:secret@localhost:5432/harbor",
    "DATA_PROVIDER_HK": "mock",
    "DATA_PROVIDER_US": "mock",
}


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


class ValidationRunCommandCliTests(unittest.TestCase):
    """Verify the ``validation freeze / tune / evaluate`` surface (SP 3.70)."""

    def test_validation_freeze_prints_frozen_status(self) -> None:
        from harbor.cli import main

        result = ValidationCommandResult(run_id="run-abc", status=ValidationStatus.DATA_FROZEN)
        output = io.StringIO()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch("harbor.cli.run_validation_command", return_value=result) as command_mock,
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["validation", "freeze", "run-abc"])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary, {"run_id": "run-abc", "status": "DATA_FROZEN"})
        self.assertEqual(command_mock.call_args.args[1], "run-abc")
        self.assertEqual(command_mock.call_args.kwargs["command"], "freeze")

    def test_validation_tune_prints_tuning_status(self) -> None:
        from harbor.cli import main

        result = ValidationCommandResult(run_id="run-x", status=ValidationStatus.TUNING)
        output = io.StringIO()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch("harbor.cli.run_validation_command", return_value=result) as command_mock,
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["validation", "tune", "run-x"])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary, {"run_id": "run-x", "status": "TUNING"})
        self.assertEqual(command_mock.call_args.kwargs["command"], "tune")

    def test_validation_evaluate_prints_evaluated_status(self) -> None:
        from harbor.cli import main

        result = ValidationCommandResult(run_id="run-y", status=ValidationStatus.EVALUATED)
        output = io.StringIO()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch("harbor.cli.run_validation_command", return_value=result) as command_mock,
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["validation", "evaluate", "run-y"])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary, {"run_id": "run-y", "status": "EVALUATED"})
        self.assertEqual(command_mock.call_args.kwargs["command"], "evaluate")

    def test_validation_state_machine_violation_is_actionable_error(self) -> None:
        from harbor.cli import main

        stderr = io.StringIO()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch(
                "harbor.cli.run_validation_command",
                side_effect=ValidationServiceError(
                    "Validation command 'evaluate' is not allowed for run 'run-abc' "
                    "in status DRAFT; valid commands follow DRAFT -> DATA_FROZEN -> "
                    "TUNING -> TEST_LOCKED -> EVALUATED (SP 3.70)."
                ),
            ),
        ):
            with self.assertRaises(SystemExit) as exit_context:
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    main(["validation", "evaluate", "run-abc"])

        self.assertEqual(exit_context.exception.code, 2)
        message = stderr.getvalue()
        self.assertIn("Validation evaluate failed", message)
        self.assertIn("DRAFT -> DATA_FROZEN -> TUNING -> TEST_LOCKED -> EVALUATED", message)


class AdvanceValidationTests(unittest.TestCase):
    """The state-machine command transitions and violations (SP 3.70)."""

    def test_freeze_transitions_draft_to_frozen(self) -> None:
        result = advance_validation("run-1", ValidationStatus.DRAFT, command="freeze")
        self.assertEqual(result.run_id, "run-1")
        self.assertEqual(result.status, ValidationStatus.DATA_FROZEN)

    def test_tune_transitions_frozen_to_tuning(self) -> None:
        result = advance_validation("run-1", ValidationStatus.DATA_FROZEN, command="tune")
        self.assertEqual(result.status, ValidationStatus.TUNING)

    def test_evaluate_transitions_locked_to_evaluated(self) -> None:
        result = advance_validation("run-1", ValidationStatus.TEST_LOCKED, command="evaluate")
        self.assertEqual(result.status, ValidationStatus.EVALUATED)

    def test_freeze_on_frozen_is_actionable_error(self) -> None:
        with self.assertRaises(ValidationServiceError) as context:
            advance_validation("run-1", ValidationStatus.DATA_FROZEN, command="freeze")
        message = str(context.exception)
        self.assertIn("'freeze' is not allowed", message)
        self.assertIn("DATA_FROZEN", message)
        self.assertIn("EVALUATED", message)

    def test_tune_on_draft_is_actionable_error(self) -> None:
        with self.assertRaises(ValidationServiceError):
            advance_validation("run-1", ValidationStatus.DRAFT, command="tune")

    def test_evaluate_on_draft_is_actionable_error(self) -> None:
        with self.assertRaises(ValidationServiceError) as context:
            advance_validation("run-1", ValidationStatus.DRAFT, command="evaluate")
        message = str(context.exception)
        self.assertIn("'evaluate' is not allowed", message)
        self.assertIn("DRAFT -> DATA_FROZEN", message)

    def test_unknown_command_is_error(self) -> None:
        with self.assertRaises(ValidationServiceError):
            advance_validation("run-1", ValidationStatus.DRAFT, command="export")


def _run_row() -> dict[str, object]:
    """A persisted validation-run row (SP 3.12)."""
    return {
        "run_id": "run-1",
        "status": "EVALUATED",
        "config_hash": "cfg-hash",
        "config_snapshot": {
            "rolling": {"mode": "EXPANDING"},
            "budget": {"max_trials": 3},
            "tuning": {"primary_metric": "sharpe"},
        },
        "code_version": "1.0.0",
        "test_set_id": "holdout-1",
    }


def _split_row() -> dict[str, object]:
    """A persisted frozen split row."""
    return {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": date(2023, 1, 1),
        "test_end": date(2026, 12, 30),
    }


def _manifest_row() -> dict[str, object]:
    """A persisted dataset-manifest row."""
    return {
        "fingerprint": "manifest-fp",
        "markets": ["HK"],
        "base_currency": "HKD",
        "data_cutoff": date(2026, 12, 30),
        "calendar_version": "cal-1",
        "fx_source": "fx-1",
    }


def _conclusion_row() -> dict[str, object]:
    """A persisted conclusion row."""
    return {
        "conclusion": "QUALIFIED",
        "evidence": {"fingerprint": "conclusion-fp"},
        "limitations": [{"message": "limited OOS horizon"}],
    }


def _artifact_kwargs() -> dict[str, object]:
    """The row inputs for the SP 3.71 artifact builder."""
    return {
        "run_row": _run_row(),
        "manifest_row": _manifest_row(),
        "split_row": _split_row(),
        "conclusion_row": _conclusion_row(),
        "trial_rows": ({"trial_id": "t-1"}, {"trial_id": "t-2"}),
        "fold_rows": (
            {"fold_index": 0, "backtest_run_id": "oos-run-0"},
            {"fold_index": 1, "backtest_run_id": "oos-run-1"},
        ),
        "stress_rows": (
            {
                "scenario_type": "cost",
                "scenario_name": "cost-stress-2x",
                "applicable_markets": ["HK"],
                "delta": {"net_value_impact_pct": -1.5},
            },
        ),
        "warning_rows": (
            {
                "warning_code": "w1",
                "severity": "warning",
                "message": "fx missing",
                "created_at": datetime(2026, 1, 1),
            },
        ),
    }


class ValidationShowReportCliTests(unittest.TestCase):
    """Verify the ``validation show / report`` surface (SP 3.71)."""

    def test_validation_show_prints_status_view(self) -> None:
        from harbor.cli import main

        result = ValidationShowResult(
            run_id="run-1",
            status="EVALUATED",
            config_hash="cfg",
            code_version="1.0.0",
            test_set_id="holdout-1",
            split={"test_end": "2026-12-30"},
            dataset_fingerprint="mf",
            trial_count=2,
            fold_count=4,
            stress_count=1,
            warning_count=0,
            conclusion="QUALIFIED",
        )
        output = io.StringIO()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch("harbor.cli.show_validation", return_value=result) as show_mock,
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["validation", "show", "run-1"])

        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["status"], "EVALUATED")
        self.assertEqual(summary["fold_count"], 4)
        self.assertEqual(summary["conclusion"], "QUALIFIED")
        self.assertEqual(show_mock.call_args.kwargs["run_id"], "run-1")

    def test_validation_report_json_prints_document(self) -> None:
        from harbor.cli import main

        output = io.StringIO()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch(
                "harbor.cli.report_validation",
                return_value='{\n  "run": {"run_id": "run-1"}\n}',
            ) as report_mock,
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["validation", "report", "run-1"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["run"]["run_id"], "run-1")
        self.assertEqual(report_mock.call_args.kwargs["report_format"], "json")

    def test_validation_report_csv_format(self) -> None:
        from harbor.cli import main

        output = io.StringIO()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch(
                "harbor.cli.report_validation",
                return_value="# folds\nvalidation_run_id,fold_index\nrun-1,0\n",
            ) as report_mock,
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["validation", "report", "run-1", "--format", "csv"])

        self.assertEqual(exit_code, 0)
        self.assertIn("# folds", output.getvalue())
        self.assertEqual(report_mock.call_args.kwargs["report_format"], "csv")

    def test_validation_report_html_format(self) -> None:
        from harbor.cli import main

        output = io.StringIO()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch(
                "harbor.cli.report_validation",
                return_value="<!doctype html><html></html>",
            ) as report_mock,
        ):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                exit_code = main(["validation", "report", "run-1", "--format", "html"])

        self.assertEqual(exit_code, 0)
        self.assertIn("<!doctype html>", output.getvalue())
        self.assertEqual(report_mock.call_args.kwargs["report_format"], "html")

    def test_validation_show_missing_run_is_actionable_error(self) -> None:
        from harbor.cli import main

        stderr = io.StringIO()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("harbor.cli.create_engine"),
            patch(
                "harbor.cli.show_validation",
                side_effect=ValidationShowError("No validation run found for run id 'nope'."),
            ),
        ):
            with self.assertRaises(SystemExit) as exit_context:
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    main(["validation", "show", "nope"])

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("Validation show failed", stderr.getvalue())


class ValidationShowReportServiceTests(unittest.TestCase):
    """The DB-free show/report helpers (SP 3.71)."""

    def test_show_from_rows_assembles_status_view(self) -> None:
        result = _show_from_rows(
            _run_row(),
            (_split_row(),),
            (_manifest_row(),),
            (_conclusion_row(),),
            trial_count=2,
            fold_count=4,
            stress_count=1,
            warning_count=1,
        )
        self.assertEqual(result.status, "EVALUATED")
        self.assertEqual(result.split["test_end"], "2026-12-30")
        self.assertEqual(result.dataset_fingerprint, "manifest-fp")
        self.assertEqual(result.trial_count, 2)
        self.assertEqual(result.conclusion, "QUALIFIED")
        rendered = result.to_dict()
        self.assertEqual(rendered["run_id"], "run-1")
        self.assertEqual(rendered["test_set_id"], "holdout-1")

    def test_report_artifact_from_rows_assembles_sections(self) -> None:
        artifact = _report_artifact_from_rows(**_artifact_kwargs())  # type: ignore[arg-type]
        self.assertEqual(artifact["run"]["run_id"], "run-1")
        self.assertEqual(artifact["frozen_config"]["split"]["test_end"], "2026-12-30")
        self.assertEqual(artifact["frozen_config"]["budget"], {"max_trials": 3})
        self.assertEqual(artifact["dataset"]["fingerprint"], "manifest-fp")
        self.assertEqual(len(artifact["trial_log"]), 2)
        self.assertEqual(artifact["fold_results"][0]["run_id"], "oos-run-0")
        self.assertEqual(
            artifact["stress_results"]["registrations"][0]["baseline_difference"], -1.5
        )
        self.assertEqual(artifact["conclusion"]["overall"], "QUALIFIED")
        self.assertEqual(artifact["conclusion"]["test_set_version"], "holdout-1")
        self.assertEqual(artifact["conclusion"]["conclusion_fingerprint"], "conclusion-fp")
        self.assertEqual(len(artifact["audit_events"]), 1)
        self.assertEqual(artifact["audit_events"][0]["message"], "fx missing")

    def test_render_json(self) -> None:
        artifact = _report_artifact_from_rows(**_artifact_kwargs())  # type: ignore[arg-type]
        text = _render_report(artifact, "json")
        self.assertEqual(json.loads(text)["run"]["run_id"], "run-1")

    def test_render_csv(self) -> None:
        artifact = _report_artifact_from_rows(**_artifact_kwargs())  # type: ignore[arg-type]
        text = _render_report(artifact, "csv")
        self.assertIn("# folds", text)
        self.assertIn("validation_run_id,fold_index", text)
        self.assertIn("# stress_differences", text)

    def test_render_html(self) -> None:
        artifact = _report_artifact_from_rows(**_artifact_kwargs())  # type: ignore[arg-type]
        text = _render_report(artifact, "html", limitations=("limited OOS horizon",))
        self.assertIn("<!doctype html>", text)
        self.assertIn("limited OOS horizon", text)
        self.assertIn("QUALIFIED", text)

    def test_render_unknown_format_raises(self) -> None:
        artifact = _report_artifact_from_rows(**_artifact_kwargs())  # type: ignore[arg-type]
        with self.assertRaises(ValidationReportError):
            _render_report(artifact, "xml")


if __name__ == "__main__":
    unittest.main()
