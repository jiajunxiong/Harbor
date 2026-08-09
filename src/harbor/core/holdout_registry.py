"""Independent holdout (test set) registration and access guard (MVP 3 / SP 3.5).

Registers the final test set before evaluation — its purpose, UTC creation
time, the validation stage that authorizes it and (once read) the first time
it was read — and guards it so parameter selection can never read the test
interval before final evaluation. The registration is an immutable value;
recording the first read returns a new record, preserving the audit trail.

The recorded ``config_hash`` is the SP 3.3 frozen-split hash, and the access
rules hook into the SP 3.13 validation state machine: a test set is only
authorized from ``TEST_LOCKED`` and is unlocked exactly once for final
evaluation (SP 3.41). Core layer: depends only on the validation-domain
types, never on storage, services or CLI code.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum

from harbor.core.validation_domain import EvaluationSplit, ValidationStatus

_TEST_ACCESS_STAGES = (ValidationStatus.TEST_LOCKED, ValidationStatus.EVALUATED)


class HoldoutPurpose(StrEnum):
    """The legitimate purpose of an independent holdout (SP 3.5).

    The only purpose in this system is final evaluation; a holdout is never
    used for parameter selection.
    """

    FINAL_EVALUATION = "final_evaluation"


class HoldoutRegistrationError(ValueError):
    """Raised when a test-set registration is invalid or duplicated (SP 3.5)."""


class HoldoutAccessError(ValueError):
    """Raised when the test set is accessed before it is authorized (SP 3.5)."""


def _require_utc_aware(timestamp: datetime) -> None:
    """Require an explicit UTC offset so timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("Test-set timestamps must be UTC-aware (offset 0).")


@dataclass(frozen=True)
class HoldoutRegistration:
    """Immutable registration of the independent holdout (SP 3.5).

    Records the test set id, its purpose, the UTC creation time, the stage
    that authorizes reading it (``TEST_LOCKED``) and — once final evaluation
    has read it — the first-read time. ``split`` carries the frozen test
    boundaries (SP 3.1 / 3.4) and ``config_hash`` the frozen-split hash
    (SP 3.3).
    """

    test_set_id: str
    purpose: HoldoutPurpose = HoldoutPurpose.FINAL_EVALUATION
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    authorized_stage: ValidationStatus = ValidationStatus.TEST_LOCKED
    split: EvaluationSplit | None = None
    config_hash: str | None = None
    first_read_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.test_set_id:
            raise ValueError("Test set id must be non-empty.")
        _require_utc_aware(self.created_at)
        if self.authorized_stage is not ValidationStatus.TEST_LOCKED:
            raise ValueError(
                f"A test set is only authorized at TEST_LOCKED, not {self.authorized_stage.value}."
            )
        if self.first_read_at is not None:
            _require_utc_aware(self.first_read_at)
            if self.first_read_at < self.created_at:
                raise ValueError("Test set first read must not precede its creation.")

    @property
    def test_start(self) -> date | None:
        """First guarded test day, when a split is recorded."""
        return self.split.test_start if self.split is not None else None

    @property
    def test_end(self) -> date | None:
        """Last guarded test day, when a split is recorded."""
        return self.split.test_end if self.split is not None else None

    def readable(self) -> str:
        """Render the registration as a single line."""
        read = self.first_read_at.isoformat() if self.first_read_at is not None else "not read"
        split = self.split.readable() if self.split is not None else "split not recorded"
        return (
            f"test set {self.test_set_id} purpose {self.purpose.value} "
            f"created {self.created_at.isoformat()} "
            f"authorized {self.authorized_stage.value} first read {read} "
            f"config {self.config_hash or 'none'} | {split}"
        )


def register_test_set(
    test_set_id: str,
    *,
    purpose: HoldoutPurpose = HoldoutPurpose.FINAL_EVALUATION,
    split: EvaluationSplit | None = None,
    config_hash: str | None = None,
    created_at: datetime | None = None,
) -> HoldoutRegistration:
    """Register the independent holdout for a validation run (SP 3.5).

    Args:
        test_set_id: Unique identifier of the holdout.
        purpose: Intended use; final evaluation by default.
        split: Frozen train/validation/test boundaries (SP 3.1 / 3.4).
        config_hash: Frozen-split hash from the validated config (SP 3.3).
        created_at: UTC creation time; defaults to now.

    Returns:
        The immutable registration record.
    """
    return HoldoutRegistration(
        test_set_id=test_set_id,
        purpose=purpose,
        created_at=created_at if created_at is not None else datetime.now(timezone.utc),
        authorized_stage=ValidationStatus.TEST_LOCKED,
        split=split,
        config_hash=config_hash,
    )


def guard_final_evaluation_read(
    registration: HoldoutRegistration | None,
    current_stage: ValidationStatus,
) -> None:
    """Reject reading the test set before it is authorized (SP 3.5 / 3.41).

    The independent holdout may only be read once the validation run has
    locked it for final evaluation (``TEST_LOCKED``) or later
    (``EVALUATED``); any attempt during tuning or earlier is rejected.

    Raises:
        HoldoutAccessError: If nothing is registered or the stage is not
            authorized for final-evaluation reading.
    """
    if registration is None:
        raise HoldoutAccessError("No test set is registered; cannot read the holdout.")
    if current_stage not in _TEST_ACCESS_STAGES:
        raise HoldoutAccessError(
            f"Test set {registration.test_set_id} is not readable at stage "
            f"{current_stage.value}; the independent holdout is only "
            "authorized for final evaluation from TEST_LOCKED."
        )


def guard_parameter_selection(
    registration: HoldoutRegistration | None,
    current_stage: ValidationStatus,
) -> None:
    """Reject any parameter-selection use of the test set (SP 3.5).

    Parameter selection is restricted to training and validation data and may
    never read the independent holdout — before or after final evaluation
    (SP 3.21, 3.24).

    Raises:
        HoldoutAccessError: Always, with the test set id and stage named.
    """
    test_set = (
        f"test set {registration.test_set_id}"
        if registration is not None
        else "the unregistered test set"
    )
    raise HoldoutAccessError(
        f"Parameter selection cannot use {test_set} at stage "
        f"{current_stage.value}; selection is restricted to training and "
        "validation data."
    )


def mark_first_read(
    registration: HoldoutRegistration,
    current_stage: ValidationStatus,
    read_at: datetime | None = None,
) -> HoldoutRegistration:
    """Record the first-read time of the test set (audit event, SP 3.5).

    Returns a NEW registration with ``first_read_at`` set; the original
    immutable record is unchanged. The first read is recorded exactly once
    and only once the run is authorized for final evaluation.

    Raises:
        HoldoutAccessError: If the run is not authorized to read the test set.
        HoldoutRegistrationError: If the test set was already read.
    """
    guard_final_evaluation_read(registration, current_stage)
    if registration.first_read_at is not None:
        raise HoldoutRegistrationError(
            f"Test set {registration.test_set_id} was already read at "
            f"{registration.first_read_at.isoformat()}."
        )
    timestamp = read_at if read_at is not None else datetime.now(timezone.utc)
    return replace(registration, first_read_at=timestamp)
