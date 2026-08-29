"""Validation run, status and report service (MVP 3 / SP 3.69–3.71).

Creates a DRAFT validation run from a versioned validation-config file
(SP 3.69): loads and validates the SP 3.3 configuration, assigns a new run id
and creates the SP 3.13 DRAFT state, returning the run id and status so the
CLI can surface ``harbor-cli validation run --config <path>``.

Applies the state-machine commands (SP 3.70): ``freeze`` (DRAFT ->
DATA_FROZEN), ``tune`` (DATA_FROZEN -> TUNING) and ``evaluate``
(TEST_LOCKED -> EVALUATED) through the SP 3.13 state machine. A command
that violates the state machine raises an actionable error naming the
required order, and the CLI surfaces it as an exit-code-2 usage error
(执行顺序违反状态机时给出可行动错误).

Assembles the status view and research report (SP 3.71): ``show_validation``
renders a run's status, frozen split, dataset fingerprint and artifact counts;
``report_validation`` reassembles an SP 3.66-shaped artifact from the SP 3.12
persisted rows and renders it as JSON (SP 3.66 shape), CSV (SP 3.67 tables)
or HTML (SP 3.68), so ``harbor-cli validation show <run-id>`` and
``report <run-id> --format json/csv/html`` surface exactly what the database
can honestly reproduce.

The command creates a draft by default (默认创建草稿) — the run is only
orchestrated further by these later commands. The service lives in the
orchestration layer: it composes the SP 3.3 config loading with the SP 3.13
state machine, the SP 3.12 storage repository and the SP 3.66–3.68 report
modules, keeping the CLI command thin and free of business logic.
"""

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Connection

from harbor.core.oos_csv import export_oos_csvs
from harbor.core.oos_report import render_oos_report
from harbor.core.validation_config_loader import config_hash, load_validation_config
from harbor.core.validation_domain import EvaluationSplit, ValidationStatus
from harbor.core.validation_state_machine import (
    ValidationRunState,
    ValidationStateError,
    validation_initial_state,
)
from harbor.storage.validation_repositories import ValidationRepository


class ValidationServiceError(ValueError):
    """Raised when a validation run cannot be orchestrated (SP 3.69)."""


@dataclass(frozen=True)
class ValidationCommandResult:
    """The outcome of a CLI validation run: run id and status (SP 3.69)."""

    run_id: str
    status: ValidationStatus


def _split_hash(split: EvaluationSplit) -> str:
    """A stable SHA-256 over the frozen split boundaries (SP 3.12).

    The digest covers the six boundary dates of the SP 3.4 ``EvaluationSplit``
    so two equal splits hash identically independent of key order, and the
    value is recorded in ``validation_splits.split_hash``.
    """
    payload = json.dumps(
        {
            "train_start": split.train_start.isoformat(),
            "train_end": split.train_end.isoformat(),
            "validation_start": split.validation_start.isoformat(),
            "validation_end": split.validation_end.isoformat(),
            "test_start": split.test_start.isoformat(),
            "test_end": split.test_end.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_validation_from_config(
    config_path: str | Path,
    connection: Connection,
) -> ValidationCommandResult:
    """Create a persisted DRAFT validation run from a config file (SP 3.69).

    Loads and validates the SP 3.3 validation config — a missing file, an
    unreadable file or an invalid configuration raises ``ValueError`` — then
    assigns a new run id, creates the SP 3.13 DRAFT state and persists the
    master row via the SP 3.12 repository. The run id is therefore usable by
    the later ``freeze`` / ``tune`` / ``evaluate`` / ``show`` / ``report``
    commands, which read the run back from the database (SP 3.70).

    Args:
        config_path: Path to the YAML/JSON validation configuration.
        connection: The database connection used to persist the run.

    Returns:
        The new run id and its DRAFT status.
    """
    config = load_validation_config(config_path)
    repository = ValidationRepository(connection)
    run_id = uuid.uuid4().hex
    state = validation_initial_state(run_id)
    repository.create_run(
        run_id=run_id,
        config_hash=config_hash(config),
        config_snapshot=config.model_dump(mode="json"),
        code_version=config.code_version,
        created_at=datetime.now(timezone.utc),
        status=ValidationStatus.DRAFT.value,
    )
    # Persist the frozen split (SP 3.12) so ``show`` and ``report`` can render
    # the split diagram; the split is immutable per config and written once.
    split = config.split.to_evaluation_split()
    repository.upsert_split(
        validation_run_id=run_id,
        values={
            "split_hash": _split_hash(split),
            "train_start": split.train_start,
            "train_end": split.train_end,
            "validation_start": split.validation_start,
            "validation_end": split.validation_end,
            "test_start": split.test_start,
            "test_end": split.test_end,
        },
    )
    return ValidationCommandResult(run_id=state.run_id, status=state.status)


def advance_validation(
    run_id: str,
    current_status: ValidationStatus,
    *,
    command: str,
) -> ValidationCommandResult:
    """Apply one state-machine command to a run's current status (SP 3.70).

    ``freeze`` (DRAFT -> DATA_FROZEN), ``tune`` (DATA_FROZEN -> TUNING),
    ``lock`` (DATA_FROZEN/TUNING -> TEST_LOCKED) and ``evaluate``
    (TEST_LOCKED -> EVALUATED) are applied through the SP 3.13 state machine;
    a command that violates the state machine raises an actionable error that
    names the required order.

    Raises:
        ValidationServiceError: If ``command`` is unknown or is not an
            allowed transition from ``current_status``.
    """
    state = ValidationRunState(run_id=run_id, status=current_status)
    try:
        if command == "freeze":
            state = state.freeze()
        elif command == "tune":
            state = state.tune()
        elif command == "lock":
            state = state.lock_test_set()
        elif command == "evaluate":
            state = state.evaluate()
        else:
            raise ValidationServiceError(f"Unsupported validation command {command!r}.")
    except ValidationStateError as error:
        raise ValidationServiceError(
            f"Validation command '{command}' is not allowed for run {run_id!r} "
            f"in status {current_status.value}; valid commands follow "
            "DRAFT -> DATA_FROZEN -> TUNING -> TEST_LOCKED -> EVALUATED (SP 3.70)."
        ) from error
    return ValidationCommandResult(run_id=state.run_id, status=state.status)


def run_validation_command(
    connection: Connection,
    run_id: str,
    *,
    command: str,
) -> ValidationCommandResult:
    """Apply a state-machine command to a persisted validation run (SP 3.70).

    Loads the run's current status from the SP 3.12 repository, applies
    ``command`` through the SP 3.13 state machine, persists the new status
    and returns the run id and new status.

    Raises:
        ValidationServiceError: If the run is unknown or ``command`` is not
            an allowed transition from its current status.
    """
    repository = ValidationRepository(connection)
    row = connection.execute(repository.get_run(run_id)).first()
    if row is None:
        raise ValidationServiceError(f"Unknown validation run {run_id!r}.")
    current = ValidationStatus(row.status)
    result = advance_validation(run_id, current, command=command)
    repository.update_run(
        run_id=result.run_id,
        status=result.status.value,
        updated_at=datetime.now(timezone.utc),
    )
    return result


class ValidationShowError(ValueError):
    """Raised when a validation run's status view cannot be assembled (SP 3.71)."""


class ValidationReportError(ValueError):
    """Raised when a validation run's report cannot be rendered (SP 3.71)."""


@dataclass(frozen=True)
class ValidationShowResult:
    """The status view of a validation run (SP 3.71)."""

    run_id: str
    status: str
    config_hash: str
    code_version: str
    test_set_id: str | None
    split: Mapping[str, object]
    dataset_fingerprint: str | None
    trial_count: int
    fold_count: int
    stress_count: int
    warning_count: int
    conclusion: str | None

    def to_dict(self) -> dict[str, object]:
        """Render the status view as a JSON-safe dict."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "config_hash": self.config_hash,
            "code_version": self.code_version,
            "test_set_id": self.test_set_id,
            "split": dict(self.split),
            "dataset_fingerprint": self.dataset_fingerprint,
            "trial_count": self.trial_count,
            "fold_count": self.fold_count,
            "stress_count": self.stress_count,
            "warning_count": self.warning_count,
            "conclusion": self.conclusion,
        }


def _split_dict(split_row: Mapping[str, Any]) -> dict[str, object]:
    """The frozen split as a stable date-string dict (SP 3.71)."""
    return {
        "train_start": str(split_row["train_start"]),
        "train_end": str(split_row["train_end"]),
        "validation_start": str(split_row["validation_start"]),
        "validation_end": str(split_row["validation_end"]),
        "test_start": str(split_row["test_start"]),
        "test_end": str(split_row["test_end"]),
    }


def _show_from_rows(
    run_row: Mapping[str, Any],
    split_rows: Sequence[Mapping[str, Any]],
    manifest_rows: Sequence[Mapping[str, Any]],
    conclusion_rows: Sequence[Mapping[str, Any]],
    trial_count: int,
    fold_count: int,
    stress_count: int,
    warning_count: int,
) -> ValidationShowResult:
    """Assemble the status view from the fetched rows (SP 3.71)."""
    split = _split_dict(split_rows[0]) if split_rows else {}
    return ValidationShowResult(
        run_id=str(run_row["run_id"]),
        status=str(run_row["status"]),
        config_hash=str(run_row["config_hash"]),
        code_version=str(run_row["code_version"]),
        test_set_id=run_row["test_set_id"],
        split=split,
        dataset_fingerprint=manifest_rows[0]["fingerprint"] if manifest_rows else None,
        trial_count=trial_count,
        fold_count=fold_count,
        stress_count=stress_count,
        warning_count=warning_count,
        conclusion=str(conclusion_rows[0]["conclusion"]) if conclusion_rows else None,
    )


def show_validation(*, connection: Connection, run_id: str) -> ValidationShowResult:
    """Assemble the status view for a validation run id (SP 3.71).

    Raises:
        ValidationShowError: If no run exists for the id.
    """
    repository = ValidationRepository(connection)
    run_rows = [dict(row) for row in connection.execute(repository.get_run(run_id)).mappings()]
    if not run_rows:
        raise ValidationShowError(f"No validation run found for run id {run_id!r}.")
    split_rows = [dict(row) for row in connection.execute(repository.get_split(run_id)).mappings()]
    manifest_rows = [
        dict(row) for row in connection.execute(repository.get_manifest(run_id)).mappings()
    ]
    conclusion_rows = [
        dict(row) for row in connection.execute(repository.get_conclusion(run_id)).mappings()
    ]
    trial_count = len(list(connection.execute(repository.list_trials(run_id)).mappings()))
    fold_count = len(list(connection.execute(repository.list_folds(run_id)).mappings()))
    stress_count = len(list(connection.execute(repository.list_stress_results(run_id)).mappings()))
    warning_count = len(list(connection.execute(repository.list_warnings(run_id)).mappings()))
    return _show_from_rows(
        run_rows[0],
        split_rows,
        manifest_rows,
        conclusion_rows,
        trial_count,
        fold_count,
        stress_count,
        warning_count,
    )


_REPORT_FORMATS = ("json", "csv", "html")


def _trial_log_rows(trial_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    """The trial log (试验日志) from the persisted trials."""
    return [
        {
            "fold_index": index,
            "trial_id": str(row["trial_id"]),
            "trial_fingerprint": "",
        }
        for index, row in enumerate(trial_rows)
    ]


def _fold_result_rows(fold_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    """The fold results (折叠结果) from the persisted folds."""
    return [
        {
            "fold_index": int(row["fold_index"]),
            "run_id": (str(row["backtest_run_id"]) if row["backtest_run_id"] is not None else None),
            "replay_fingerprint": "",
            "report_artifact_fingerprint": "",
        }
        for row in fold_rows
    ]


def _stress_rows(stress_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    """The stress differences (压力差异) from the persisted stress results."""
    rows: list[dict[str, object]] = []
    for row in stress_rows:
        delta = dict(row.get("delta") or {})
        rows.append(
            {
                "category": str(row["scenario_type"]),
                "scenario_id": str(row["scenario_name"]),
                "market": ", ".join(row.get("applicable_markets") or []),
                "baseline_difference": delta.get("net_value_impact_pct"),
                "difference_summary": delta.get("summary"),
            }
        )
    return rows


def _warning_rows(warning_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    """The audit events (审计事件) from the persisted warnings."""
    return [
        {
            "warning_code": str(row["warning_code"]),
            "severity": str(row["severity"]),
            "message": str(row["message"]),
            "created_at": str(row["created_at"]),
        }
        for row in warning_rows
    ]


def _report_artifact_from_rows(
    run_row: Mapping[str, Any],
    manifest_row: Mapping[str, Any] | None,
    split_row: Mapping[str, Any] | None,
    conclusion_row: Mapping[str, Any] | None,
    trial_rows: Sequence[Mapping[str, Any]],
    fold_rows: Sequence[Mapping[str, Any]],
    stress_rows: Sequence[Mapping[str, Any]],
    warning_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reassemble an SP 3.66-shaped artifact from the persisted rows (SP 3.71).

    Only the sections the database can honestly reproduce are populated: the
    split / rolling / budget / tuning from the config snapshot, the trial log
    and fold results from their tables, the stress differences and the
    conclusion. Fit snapshots and replay fingerprints are not persisted, so
    they render empty rather than fabricated.
    """
    config_snapshot = dict(run_row["config_snapshot"])
    manifest = dict(manifest_row) if manifest_row else {}
    split = dict(split_row) if split_row else {}
    conclusion = dict(conclusion_row) if conclusion_row else {}
    evidence = dict(conclusion.get("evidence") or {})
    return {
        "schema_version": "1.0",
        "run": {"run_id": str(run_row["run_id"])},
        "frozen_config": {
            "split": _split_dict(split) if split else {},
            "rolling": config_snapshot.get("rolling", {}),
            "budget": config_snapshot.get("budget", {}),
            "tuning": config_snapshot.get("tuning", {}),
        },
        "dataset": {
            "fingerprint": manifest.get("fingerprint"),
            "manifest": {
                "markets": manifest.get("markets", []),
                "base_currency": manifest.get("base_currency"),
                "data_cutoff": (
                    str(manifest["data_cutoff"]) if manifest.get("data_cutoff") else None
                ),
                "calendar_version": manifest.get("calendar_version"),
                "fx_source": manifest.get("fx_source"),
            },
        },
        "trial_log": _trial_log_rows(trial_rows),
        "fit_snapshots": [],
        "fold_results": _fold_result_rows(fold_rows),
        "stress_results": {
            "version": "persisted",
            "source": "validation_stress_results",
            "registrations": _stress_rows(stress_rows),
        },
        "conclusion": {
            "overall": conclusion.get("conclusion"),
            "conclusion_fingerprint": str(evidence.get("fingerprint") or ""),
            "test_set_version": run_row["test_set_id"],
            "dataset_fingerprint": manifest.get("fingerprint"),
            "code_version": str(run_row["code_version"]),
        },
        "audit_events": _warning_rows(warning_rows),
    }


def _render_report(
    artifact: Mapping[str, Any],
    report_format: str,
    *,
    limitations: Sequence[str] = (),
) -> str:
    """Render an SP 3.66-shaped artifact in the requested format (SP 3.71)."""
    if report_format == "json":
        return json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False)
    if report_format == "csv":
        csvs = export_oos_csvs(artifact)
        return "\n\n".join(f"# {name}\n{content.rstrip()}" for name, content in csvs.items())
    if report_format == "html":
        return render_oos_report(artifact, limitations=limitations)
    raise ValidationReportError(
        f"Unknown report format {report_format!r}; expected one of {sorted(_REPORT_FORMATS)}."
    )


def report_validation(
    *,
    connection: Connection,
    run_id: str,
    report_format: str,
) -> str:
    """Render a validation run's research report in the requested format (SP 3.71).

    Args:
        connection: The database connection.
        run_id: The validation run id.
        report_format: One of ``json``, ``csv`` or ``html``.

    Returns:
        The rendered report document.

    Raises:
        ValidationReportError: If the run is missing or the format is unknown.
    """
    repository = ValidationRepository(connection)
    run_rows = [dict(row) for row in connection.execute(repository.get_run(run_id)).mappings()]
    if not run_rows:
        raise ValidationReportError(f"No validation run found for run id {run_id!r}.")
    run_row = run_rows[0]
    manifest_rows = [
        dict(row) for row in connection.execute(repository.get_manifest(run_id)).mappings()
    ]
    split_rows = [dict(row) for row in connection.execute(repository.get_split(run_id)).mappings()]
    conclusion_rows = [
        dict(row) for row in connection.execute(repository.get_conclusion(run_id)).mappings()
    ]
    trial_rows = [
        dict(row) for row in connection.execute(repository.list_trials(run_id)).mappings()
    ]
    fold_rows = [dict(row) for row in connection.execute(repository.list_folds(run_id)).mappings()]
    stress_rows = [
        dict(row) for row in connection.execute(repository.list_stress_results(run_id)).mappings()
    ]
    warning_rows = [
        dict(row) for row in connection.execute(repository.list_warnings(run_id)).mappings()
    ]
    artifact = _report_artifact_from_rows(
        run_row,
        manifest_rows[0] if manifest_rows else None,
        split_rows[0] if split_rows else None,
        conclusion_rows[0] if conclusion_rows else None,
        trial_rows,
        fold_rows,
        stress_rows,
        warning_rows,
    )
    limitations = tuple(
        str(item.get("message")) if isinstance(item, dict) and item.get("message") else str(item)
        for item in (conclusion_rows[0].get("limitations", []) if conclusion_rows else [])
    )
    return _render_report(artifact, report_format, limitations=limitations)
