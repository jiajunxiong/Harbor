"""Validation run, status and report service (MVP 3 / SP 3.69–3.70).

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

The command creates a draft by default (默认创建草稿) — the run is only
orchestrated further by these later commands. The service lives in the
orchestration layer: it composes the SP 3.3 config loading with the SP 3.13
state machine and the SP 3.12 storage repository, keeping the CLI command
thin and free of business logic.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import Connection

from harbor.core.validation_config_loader import load_validation_config
from harbor.core.validation_domain import ValidationStatus
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


def run_validation_from_config(config_path: str | Path) -> ValidationCommandResult:
    """Create a DRAFT validation run from a config file (SP 3.69).

    Loads and validates the SP 3.3 validation config — a missing file, an
    unreadable file or an invalid configuration raises ``ValueError`` — then
    assigns a new run id and creates the SP 3.13 DRAFT state.

    Args:
        config_path: Path to the YAML/JSON validation configuration.

    Returns:
        The new run id and its DRAFT status.
    """
    load_validation_config(config_path)
    run_id = uuid.uuid4().hex
    state = validation_initial_state(run_id)
    return ValidationCommandResult(run_id=state.run_id, status=state.status)


def advance_validation(
    run_id: str,
    current_status: ValidationStatus,
    *,
    command: str,
) -> ValidationCommandResult:
    """Apply one state-machine command to a run's current status (SP 3.70).

    ``freeze`` (DRAFT -> DATA_FROZEN), ``tune`` (DATA_FROZEN -> TUNING) and
    ``evaluate`` (TEST_LOCKED -> EVALUATED) are applied through the SP 3.13
    state machine; a command that violates the state machine raises an
    actionable error that names the required order.

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
