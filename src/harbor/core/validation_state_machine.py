"""Auditable validation state machine (MVP 3 / SP 3.13).

Defines the lifecycle of a validation run over the SP 3.1 ``ValidationStatus``
vocabulary: ``DRAFT`` -> ``DATA_FROZEN`` -> ``TUNING`` -> ``TEST_LOCKED`` ->
``EVALUATED``. A run freezes its dataset and split first (``DATA_FROZEN``),
then either tunes parameters on the train/validation interval (``TUNING``) or
locks the test set directly for a pre-registered baseline (``TEST_LOCKED``).
``DATA_FROZEN`` may also land in ``NOT_QUALIFIED`` when the coverage gates
(SP 3.10) block the frozen dataset. ``TEST_LOCKED`` unlocks the independent
holdout exactly once for final evaluation (``EVALUATED``); an evaluation that
cannot satisfy the stability rules degrades to ``NOT_QUALIFIED``. An
execution failure in any active state lands in ``FAILED``. ``NOT_QUALIFIED``
and ``FAILED`` are terminal: a failed run is never silently resumed — it must
be re-created as a new validation run (SP 3.43).

Every transition is recorded in an immutable audit trail: a
``ValidationTransition`` entry with the source/target statuses, a UTC
timestamp and a reason. The chain is self-validating (each transition must
continue from the previous target and end at the current status), so the
state machine is fully auditable and replayable. Pure core logic; the storage
layer (SP 3.12) persists the status.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from harbor.core.validation_domain import ValidationStatus

_ALLOWED: dict[ValidationStatus, frozenset[ValidationStatus]] = {
    ValidationStatus.DRAFT: frozenset({ValidationStatus.DATA_FROZEN, ValidationStatus.FAILED}),
    ValidationStatus.DATA_FROZEN: frozenset(
        {
            ValidationStatus.TUNING,
            ValidationStatus.TEST_LOCKED,
            ValidationStatus.NOT_QUALIFIED,
            ValidationStatus.FAILED,
        }
    ),
    ValidationStatus.TUNING: frozenset(
        {
            ValidationStatus.TEST_LOCKED,
            ValidationStatus.NOT_QUALIFIED,
            ValidationStatus.FAILED,
        }
    ),
    ValidationStatus.TEST_LOCKED: frozenset(
        {ValidationStatus.EVALUATED, ValidationStatus.NOT_QUALIFIED, ValidationStatus.FAILED}
    ),
    ValidationStatus.EVALUATED: frozenset({ValidationStatus.NOT_QUALIFIED}),
    ValidationStatus.NOT_QUALIFIED: frozenset(),
    ValidationStatus.FAILED: frozenset(),
}

_TEST_ACCESS_STAGES = (ValidationStatus.TEST_LOCKED, ValidationStatus.EVALUATED)


class ValidationStateError(ValueError):
    """Raised when a validation run attempts an invalid transition (SP 3.13)."""


def _require_utc_aware(timestamp: datetime) -> None:
    """Require an explicit UTC offset so audit timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("Validation transition timestamps must be UTC-aware (offset 0).")


def allowed_transitions(status: ValidationStatus) -> frozenset[ValidationStatus]:
    """Return the states reachable directly from ``status`` (SP 3.13)."""
    return _ALLOWED[status]


def can_transition(current: ValidationStatus, new: ValidationStatus) -> bool:
    """Whether ``current`` may transition to ``new`` (SP 3.13)."""
    return new in _ALLOWED[current]


def is_test_authorized(status: ValidationStatus) -> bool:
    """Whether ``status`` may read the independent test set (SP 3.13 / 3.24).

    The test interval is readable only after it has been locked for final
    evaluation (``TEST_LOCKED``) and remains readable while ``EVALUATED``;
    any earlier stage (DRAFT / DATA_FROZEN / TUNING) is denied.
    """
    return status in _TEST_ACCESS_STAGES


def require_test_authorized(status: ValidationStatus) -> None:
    """Raise unless ``status`` may read the independent test set (SP 3.24).

    Raises:
        ValidationStateError: If the test set is not readable at ``status``.
    """
    if not is_test_authorized(status):
        raise ValidationStateError(
            f"test set is not readable at stage {status.value}; it is only authorized "
            "from TEST_LOCKED for final evaluation."
        )


@dataclass(frozen=True)
class ValidationTransition:
    """One recorded state transition in the audit trail (SP 3.13).

    Records the source and target statuses, the UTC timestamp and an optional
    human-readable reason so every stage of a validation run is auditable.
    """

    from_status: ValidationStatus
    to_status: ValidationStatus
    recorded_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_utc_aware(self.recorded_at)

    def readable(self) -> str:
        """Render the transition as one audit line."""
        reason = f" ({self.reason})" if self.reason is not None else ""
        return (
            f"{self.from_status.value} -> {self.to_status.value} "
            f"at {self.recorded_at.isoformat()}{reason}"
        )


@dataclass(frozen=True)
class ValidationDiagnostics:
    """Diagnostics retained when a validation run fails or is not qualified.

    ``warnings`` accumulate while the run is in progress; ``error_summary``
    and ``stage`` are set when the run fails; ``reason`` records why the run
    was marked ``NOT_QUALIFIED`` (coverage gap, leakage or unregistered
    experiment — never a silent pass).
    """

    warnings: tuple[str, ...] = ()
    error_summary: str | None = None
    stage: str | None = None
    reason: str | None = None

    def readable(self) -> str:
        """Render the diagnostics as human-readable lines."""
        lines: list[str] = []
        if self.stage is not None:
            lines.append(f"  stage: {self.stage}")
        if self.error_summary is not None:
            lines.append(f"  error: {self.error_summary}")
        if self.reason is not None:
            lines.append(f"  reason: {self.reason}")
        for warning in self.warnings:
            lines.append(f"  warning: {warning}")
        return "\n".join(lines)


def _validate_transition_chain(
    status: ValidationStatus, transitions: tuple[ValidationTransition, ...]
) -> None:
    """Require the audit trail to be a consistent chain ending at ``status``.

    Each transition must follow from the previous target, and the final
    transition must target the current status — so a hand-edited or truncated
    audit trail is rejected instead of silently trusted.
    """
    previous_target: ValidationStatus | None = None
    for index, entry in enumerate(transitions):
        if previous_target is not None and entry.from_status is not previous_target:
            raise ValueError(
                f"transition chain is broken at entry {index}: "
                f"{entry.from_status.value} does not follow "
                f"{previous_target.value}."
            )
        previous_target = entry.to_status
    if transitions and transitions[-1].to_status is not status:
        raise ValueError(
            f"final transition targets {transitions[-1].to_status.value}, "
            f"but the current status is {status.value}."
        )


@dataclass(frozen=True)
class ValidationRunState:
    """Immutable state of one validation run (SP 3.13)."""

    run_id: str
    status: ValidationStatus = ValidationStatus.DRAFT
    diagnostics: ValidationDiagnostics = ValidationDiagnostics()
    transitions: tuple[ValidationTransition, ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be non-empty.")
        _validate_transition_chain(self.status, self.transitions)

    def transition(
        self,
        new_status: ValidationStatus,
        *,
        reason: str | None = None,
        recorded_at: datetime | None = None,
    ) -> "ValidationRunState":
        """Transition to ``new_status``, recording an audit entry (SP 3.13).

        Raises:
            ValidationStateError: If the transition is not allowed.
        """
        if not can_transition(self.status, new_status):
            raise ValidationStateError(
                f"Invalid validation state transition for run {self.run_id!r}: "
                f"{self.status.value} -> {new_status.value}."
            )
        timestamp = recorded_at if recorded_at is not None else datetime.now(timezone.utc)
        _require_utc_aware(timestamp)
        entry = ValidationTransition(
            from_status=self.status,
            to_status=new_status,
            recorded_at=timestamp,
            reason=reason,
        )
        return replace(self, status=new_status, transitions=self.transitions + (entry,))

    def freeze(
        self, *, reason: str = "dataset and split frozen", recorded_at: datetime | None = None
    ) -> "ValidationRunState":
        """Freeze the dataset, calendar and split (DRAFT -> DATA_FROZEN)."""
        return self.transition(ValidationStatus.DATA_FROZEN, reason=reason, recorded_at=recorded_at)

    def tune(
        self,
        *,
        reason: str = "parameter tuning on train/validation",
        recorded_at: datetime | None = None,
    ) -> "ValidationRunState":
        """Begin parameter tuning on the train/validation interval (SP 3.33)."""
        return self.transition(ValidationStatus.TUNING, reason=reason, recorded_at=recorded_at)

    def lock_test_set(
        self,
        *,
        reason: str = "test set locked for final evaluation",
        recorded_at: datetime | None = None,
    ) -> "ValidationRunState":
        """Lock the independent test set (SP 3.24): TUNING/DATA_FROZEN -> TEST_LOCKED."""
        return self.transition(ValidationStatus.TEST_LOCKED, reason=reason, recorded_at=recorded_at)

    def evaluate(
        self,
        *,
        reason: str = "final evaluation on the holdout",
        recorded_at: datetime | None = None,
    ) -> "ValidationRunState":
        """Unlock and evaluate the independent holdout (SP 3.41): TEST_LOCKED -> EVALUATED."""
        return self.transition(ValidationStatus.EVALUATED, reason=reason, recorded_at=recorded_at)

    def mark_not_qualified(
        self, *, reason: str, recorded_at: datetime | None = None
    ) -> "ValidationRunState":
        """Mark the run as not qualified, retaining the reason (SP 3.13).

        Reached when the coverage gates block the frozen dataset, tuning fails
        to satisfy the pre-registered rules, or the evaluation cannot pass the
        stability checks. The reason is mandatory so no run lands here without
        an auditable explanation.

        Raises:
            ValueError: If ``reason`` is empty.
            ValidationStateError: If the current state cannot be not-qualified.
        """
        if not reason:
            raise ValueError("reason must be non-empty.")
        state = self.transition(
            ValidationStatus.NOT_QUALIFIED, reason=reason, recorded_at=recorded_at
        )
        return replace(
            state,
            diagnostics=replace(state.diagnostics, reason=reason),
        )

    def with_warning(self, message: str) -> "ValidationRunState":
        """Accumulate a diagnostic warning while the run is in progress."""
        if not message:
            raise ValueError("warning message must be non-empty.")
        return replace(
            self,
            diagnostics=replace(
                self.diagnostics,
                warnings=self.diagnostics.warnings + (message,),
            ),
        )

    def fail(
        self, error_summary: str, *, stage: str | None = None, recorded_at: datetime | None = None
    ) -> "ValidationRunState":
        """Mark the run as failed, retaining diagnostics (SP 3.13 / 3.43).

        The accumulated warnings are kept and the failure's ``error_summary``
        and ``stage`` are recorded so a failed validation run is diagnosable
        and never silently resumed.

        Raises:
            ValueError: If ``error_summary`` is empty.
            ValidationStateError: If the current state cannot fail.
        """
        if not error_summary:
            raise ValueError("error_summary must be non-empty.")
        failed = self.transition(
            ValidationStatus.FAILED, reason=error_summary, recorded_at=recorded_at
        )
        return replace(
            failed,
            diagnostics=replace(
                failed.diagnostics,
                error_summary=error_summary,
                stage=stage,
            ),
        )

    @property
    def test_authorized(self) -> bool:
        """Whether the current stage may read the independent test set."""
        return is_test_authorized(self.status)

    def readable(self) -> str:
        """Render the run state with its diagnostics and audit trail."""
        lines = [
            f"validation run {self.run_id}: {self.status.value}",
        ]
        diagnostics = self.diagnostics.readable()
        if diagnostics:
            lines.append(diagnostics)
        for entry in self.transitions:
            lines.append(f"  {entry.readable()}")
        return "\n".join(lines)


def validation_initial_state(run_id: str) -> ValidationRunState:
    """Create a new validation run in the DRAFT state (SP 3.13)."""
    return ValidationRunState(run_id=run_id, status=ValidationStatus.DRAFT)
