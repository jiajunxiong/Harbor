"""Validation run, status and report service (MVP 3 / SP 3.69).

Creates a DRAFT validation run from a versioned validation-config file
(SP 3.69): loads and validates the SP 3.3 configuration, assigns a new run id
and creates the SP 3.13 DRAFT state, returning the run id and status so the
CLI can surface ``harbor-cli validation run --config <path>``.

The command creates a draft by default (默认创建草稿) — the run is only
orchestrated further by later commands (freeze / tune / evaluate, SP 3.70).
The service lives in the orchestration layer: it composes the SP 3.3 config
loading with the SP 3.13 state machine, keeping the CLI command thin and free
of business logic.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path

from harbor.core.validation_config_loader import load_validation_config
from harbor.core.validation_domain import ValidationStatus
from harbor.core.validation_state_machine import validation_initial_state


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
