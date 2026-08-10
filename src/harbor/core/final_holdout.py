"""Final holdout execution (MVP 3 / SP 3.41).

Unlocks the independent holdout (test set) exactly once for final evaluation,
and only after the training/validation selection is frozen, then saves the
unlock event, the responsibility statement and the input fingerprint
(仅在训练/验证选择冻结后解锁一次独立保留集；解锁事件、责任说明和输入指纹必须
保存).

- **Unlock exactly once, only after the selection is frozen**: the release is
  recorded through the SP 3.5 holdout registry's ``mark_first_read``, which
  guards the stage (only ``TEST_LOCKED``/``EVALUATED`` — the run has locked
  the test set for final evaluation after tuning/selection) and rejects a
  second read (unlock once). Any premature or repeated unlock raises
  :class:`FinalHoldoutUnlockError`.
- **Unlock event** (解锁事件): the auditable :class:`HoldoutUnlockEvent` —
  test set id, the UTC unlock time (the registration's ``first_read_at``),
  the stage at unlock and the responsibility statement — is persisted on the
  release.
- **Responsibility statement** (责任说明): the authorizer who takes
  responsibility for the final evaluation is recorded verbatim.
- **Input fingerprint** (输入指纹): a stable SHA-256 over the frozen research
  inputs the final evaluation consumes — the test set id, the SP 3.7 dataset
  fingerprint, the SP 3.3 config hash, the SP 3.21 selection fingerprint and
  the code version. A convenience builder derives the dataset fingerprint and
  code version from an SP 3.35 rolling OOS run.

Pure core layer: depends only on the SP 3.5 holdout registry, the SP 3.13
state machine, the SP 3.35 rolling OOS run and the validation-domain types,
never on storage, services or CLI.
"""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from harbor.core.holdout_registry import (
    HoldoutRegistration,
    mark_first_read,
)
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.validation_domain import EvaluationSplit, ValidationStatus
from harbor.core.validation_state_machine import is_test_authorized


class FinalHoldoutError(ValueError):
    """Raised when a final-holdout execution record is invalid (SP 3.41)."""


class FinalHoldoutUnlockError(FinalHoldoutError):
    """Raised when the holdout cannot be unlocked (SP 3.41).

    Either the run has not frozen the training/validation selection yet (not
    ``TEST_LOCKED``) or the holdout was already unlocked — it is unlocked
    exactly once.
    """


def _require_utc_aware(timestamp: datetime) -> None:
    """Require an explicit UTC offset so timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise FinalHoldoutError("Final-holdout timestamps must be UTC-aware (offset 0).")


@dataclass(frozen=True)
class FinalHoldoutInputs:
    """The frozen research inputs consumed by the final evaluation (SP 3.41).

    ``test_set_id`` identifies the holdout; ``dataset_fingerprint`` is the
    SP 3.7 frozen-data fingerprint the OOS execution reads, ``config_hash``
    the SP 3.3 frozen-config hash, ``selection_fingerprint`` the SP 3.21
    frozen parameter-selection fingerprint and ``code_version`` the research
    code version. These are the inputs that must be immutable at unlock time
    (SP 3.42 re-access policy).
    """

    test_set_id: str
    dataset_fingerprint: str
    config_hash: str
    selection_fingerprint: str
    code_version: str

    def __post_init__(self) -> None:
        if not self.test_set_id:
            raise FinalHoldoutError("test set id must be non-empty.")
        if not self.dataset_fingerprint:
            raise FinalHoldoutError("dataset fingerprint must be non-empty.")
        if not self.config_hash:
            raise FinalHoldoutError("config hash must be non-empty.")
        if not self.selection_fingerprint:
            raise FinalHoldoutError("selection fingerprint must be non-empty.")
        if not self.code_version:
            raise FinalHoldoutError("code version must be non-empty.")

    def readable(self) -> str:
        """Render the frozen inputs as one line."""
        return (
            f"final-evaluation inputs test set {self.test_set_id} "
            f"dataset {self.dataset_fingerprint[:12]} config {self.config_hash} "
            f"selection {self.selection_fingerprint[:12]} code {self.code_version}"
        )


@dataclass(frozen=True)
class HoldoutUnlockEvent:
    """The auditable unlock event of the independent holdout (SP 3.41).

    Records the test set id, the UTC unlock time (equal to the registration's
    ``first_read_at``), the stage at unlock (``TEST_LOCKED``/``EVALUATED`` —
    the selection is frozen) and the responsibility statement (责任说明) of
    the authorizer.
    """

    test_set_id: str
    unlocked_at: datetime
    stage: ValidationStatus
    responsibility: str

    def __post_init__(self) -> None:
        if not self.test_set_id:
            raise FinalHoldoutError("test set id must be non-empty.")
        if not self.responsibility:
            raise FinalHoldoutError("responsibility statement must be non-empty.")
        _require_utc_aware(self.unlocked_at)
        if not is_test_authorized(self.stage):
            raise FinalHoldoutError(
                f"the holdout may only be unlocked from TEST_LOCKED, not "
                f"{self.stage.value}; the selection is not frozen yet."
            )

    def readable(self) -> str:
        """Render the unlock event as one line."""
        return (
            f"unlocked test set {self.test_set_id} at "
            f"{self.unlocked_at.isoformat()} ({self.stage.value}) by {self.responsibility}"
        )


@dataclass(frozen=True)
class FinalHoldoutRelease:
    """The saved final-holdout execution record (SP 3.41).

    Persists the unlock event (解锁事件), the post-unlock registration (which
    carries the UTC ``first_read_at`` — the unlock happened exactly once), the
    responsibility statement via the event, the frozen inputs and their
    derived ``input_fingerprint`` (输入指纹), plus the record's own derived
    SHA-256 ``fingerprint``.
    """

    unlock_event: HoldoutUnlockEvent
    registration: HoldoutRegistration
    inputs: FinalHoldoutInputs
    input_fingerprint: str
    fingerprint: str

    def __post_init__(self) -> None:
        first_read_at = self.registration.first_read_at
        if first_read_at is None:
            raise FinalHoldoutError(
                "a final-holdout release requires the holdout to be unlocked (first read recorded)."
            )
        if self.unlock_event.test_set_id != self.registration.test_set_id:
            raise FinalHoldoutError("unlock event test set must match the registration.")
        if self.unlock_event.test_set_id != self.inputs.test_set_id:
            raise FinalHoldoutError("unlock event test set must match the frozen inputs.")
        if self.unlock_event.unlocked_at != first_read_at:
            raise FinalHoldoutError("unlock event time must equal the registration's first read.")
        if self.input_fingerprint != final_holdout_input_fingerprint(self.inputs):
            raise FinalHoldoutError("input fingerprint does not match the frozen inputs.")
        if not self.fingerprint:
            raise FinalHoldoutError("final-holdout release fingerprint must be non-empty.")

    @property
    def unlocked_at(self) -> datetime:
        """The UTC time the holdout was unlocked."""
        return self.unlock_event.unlocked_at

    @property
    def stage(self) -> ValidationStatus:
        """The stage at unlock (TEST_LOCKED / EVALUATED)."""
        return self.unlock_event.stage

    @property
    def responsibility(self) -> str:
        """The responsibility statement (责任说明) of the authorizer."""
        return self.unlock_event.responsibility

    def readable(self) -> str:
        """Render the release as one line."""
        return (
            f"final holdout {self.unlock_event.test_set_id} unlocked "
            f"{self.unlocked_at.isoformat()} at {self.stage.value} by "
            f"{self.responsibility} inputs {self.input_fingerprint[:12]} "
            f"fp {self.fingerprint}"
        )


def unlock_final_holdout(
    registration: HoldoutRegistration,
    *,
    current_stage: ValidationStatus,
    responsibility: str,
    inputs: FinalHoldoutInputs,
    unlocked_at: datetime | None = None,
) -> FinalHoldoutRelease:
    """Unlock the independent holdout once for final evaluation (SP 3.41).

    Records the first read through the SP 3.5 registry (guarded to
    ``TEST_LOCKED``/``EVALUATED`` and rejected on a second read), then builds
    the auditable release with the unlock event, responsibility statement and
    the derived input fingerprint.

    Args:
        registration: The SP 3.5 holdout registration (must not be read yet).
        current_stage: The current validation stage; only ``TEST_LOCKED`` /
            ``EVALUATED`` unlock the holdout (selection frozen).
        responsibility: The responsibility statement (责任说明) of the person
            authorizing the final evaluation.
        inputs: The frozen research inputs the final evaluation consumes.
        unlocked_at: Optional UTC unlock time; defaults to now.

    Returns:
        The saved final-holdout release.

    Raises:
        FinalHoldoutUnlockError: If the selection is not frozen yet (stage
            before ``TEST_LOCKED``) or the holdout was already unlocked.
    """
    try:
        updated = mark_first_read(registration, current_stage, read_at=unlocked_at)
    except ValueError as exc:
        raise FinalHoldoutUnlockError(str(exc)) from exc
    first_read_at = updated.first_read_at
    assert first_read_at is not None
    event = HoldoutUnlockEvent(
        test_set_id=registration.test_set_id,
        unlocked_at=first_read_at,
        stage=current_stage,
        responsibility=responsibility,
    )
    release = FinalHoldoutRelease(
        unlock_event=event,
        registration=updated,
        inputs=inputs,
        input_fingerprint=final_holdout_input_fingerprint(inputs),
        fingerprint="unfingerprinted",
    )
    return replace(release, fingerprint=final_holdout_fingerprint(release))


def release_for_oos_run(
    oos_run: RollingOosRun,
    registration: HoldoutRegistration,
    *,
    current_stage: ValidationStatus,
    responsibility: str,
    config_hash: str,
    selection_fingerprint: str,
    unlocked_at: datetime | None = None,
) -> FinalHoldoutRelease:
    """Build the final-holdout release for an SP 3.35 rolling OOS run.

    Convenience factory: derives the frozen dataset fingerprint and code
    version from the OOS run (SP 3.35) so the release's input fingerprint is
    tied to exactly the execution that runs on the holdout.
    """
    inputs = FinalHoldoutInputs(
        test_set_id=registration.test_set_id,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        config_hash=config_hash,
        selection_fingerprint=selection_fingerprint,
        code_version=oos_run.code_version,
    )
    return unlock_final_holdout(
        registration,
        current_stage=current_stage,
        responsibility=responsibility,
        inputs=inputs,
        unlocked_at=unlocked_at,
    )


def _split_payload(split: EvaluationSplit | None) -> dict[str, str] | None:
    """Serialize an evaluation split's six boundaries to ISO dates."""
    if split is None:
        return None
    return {
        "train_start": split.train_start.isoformat(),
        "train_end": split.train_end.isoformat(),
        "validation_start": split.validation_start.isoformat(),
        "validation_end": split.validation_end.isoformat(),
        "test_start": split.test_start.isoformat(),
        "test_end": split.test_end.isoformat(),
    }


def final_holdout_input_json(inputs: FinalHoldoutInputs) -> str:
    """Return a stable, key-sorted JSON serialization of the frozen inputs."""
    payload: dict[str, object] = {
        "test_set_id": inputs.test_set_id,
        "dataset_fingerprint": inputs.dataset_fingerprint,
        "config_hash": inputs.config_hash,
        "selection_fingerprint": inputs.selection_fingerprint,
        "code_version": inputs.code_version,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def final_holdout_input_fingerprint(inputs: FinalHoldoutInputs) -> str:
    """Return the stable SHA-256 input fingerprint (输入指纹, SP 3.41)."""
    return hashlib.sha256(final_holdout_input_json(inputs).encode("utf-8")).hexdigest()


def final_holdout_json(release: FinalHoldoutRelease) -> str:
    """Return a stable, key-sorted JSON serialization of a release.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style);
    the derived ``input_fingerprint`` and the registration's ``first_read_at``
    (the unlock event) are included because they are recorded evidence.
    """
    registration = release.registration
    payload: dict[str, object] = {
        "test_set_id": release.unlock_event.test_set_id,
        "unlocked_at": release.unlocked_at.isoformat(),
        "stage": release.unlock_event.stage.value,
        "responsibility": release.unlock_event.responsibility,
        "input_fingerprint": release.input_fingerprint,
        "inputs": {
            "test_set_id": release.inputs.test_set_id,
            "dataset_fingerprint": release.inputs.dataset_fingerprint,
            "config_hash": release.inputs.config_hash,
            "selection_fingerprint": release.inputs.selection_fingerprint,
            "code_version": release.inputs.code_version,
        },
        "registration": {
            "test_set_id": registration.test_set_id,
            "purpose": registration.purpose.value,
            "authorized_stage": registration.authorized_stage.value,
            "config_hash": registration.config_hash,
            "first_read_at": (
                registration.first_read_at.isoformat()
                if registration.first_read_at is not None
                else None
            ),
            "split": _split_payload(registration.split),
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def final_holdout_fingerprint(release: FinalHoldoutRelease) -> str:
    """Return the stable SHA-256 fingerprint of a release (SP 3.41)."""
    return hashlib.sha256(final_holdout_json(release).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "FinalHoldoutError",
    "FinalHoldoutInputs",
    "FinalHoldoutRelease",
    "FinalHoldoutUnlockError",
    "HoldoutUnlockEvent",
    "final_holdout_fingerprint",
    "final_holdout_input_fingerprint",
    "final_holdout_input_json",
    "final_holdout_json",
    "release_for_oos_run",
    "unlock_final_holdout",
)
