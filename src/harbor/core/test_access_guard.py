"""Test-set access guard (MVP 3 / SP 3.24).

The independent test interval must never be read before it is locked for
final evaluation: before ``TEST_LOCKED`` every data read, metric computation,
report preview and parameter comparison that touches the test interval is
prohibited (在 TEST_LOCKED 前禁止数据读取、指标计算、报告预览和参数比较访问测试
区间). This module classifies the four access kinds and guards them:

- :class:`AccessKind` names the four kinds: ``DATA_READ`` (数据读取),
  ``METRIC_COMPUTATION`` (指标计算), ``REPORT_PREVIEW`` (报告预览) and
  ``PARAMETER_COMPARISON`` (参数比较).
- :func:`decide_access` is the pure decision: the three
  final-evaluation kinds are granted only when a holdout is registered (SP 3.5)
  AND the stage authorizes the test set (SP 3.13 ``is_test_authorized`` —
  ``TEST_LOCKED``/``EVALUATED``); ``PARAMETER_COMPARISON`` is ALWAYS denied
  because parameter selection never uses the test set (SP 3.21), before or
  after final evaluation.
- :class:`AccessGuard` is an immutable controller that records every
  attempt — granted or denied — as a UTC-stamped :class:`AccessAuditEntry`
  (the audit events SP 3.26 asserts on), and raises :class:`AccessGuardError`
  on any denied access via :meth:`~AccessGuard.require`.

Pure core layer: depends on the SP 3.5 holdout registry and the SP 3.13 state
machine, never on storage, services or CLI code.
"""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from harbor.core.holdout_registry import HoldoutRegistration
from harbor.core.validation_domain import ValidationStatus
from harbor.core.validation_state_machine import is_test_authorized


class AccessGuardError(ValueError):
    """Raised when test-set access is attempted before it is authorized (SP 3.24)."""


class AccessKind(StrEnum):
    """The kinds of test-interval access a validation run may request (SP 3.24).

    ``DATA_READ`` (数据读取), ``METRIC_COMPUTATION`` (指标计算) and
    ``REPORT_PREVIEW`` (报告预览) are final-evaluation accesses gated by the
    SP 3.13 stage; ``PARAMETER_COMPARISON`` (参数比较) is never permitted
    because parameter selection is restricted to training and validation data
    (SP 3.21).
    """

    DATA_READ = "data_read"
    METRIC_COMPUTATION = "metric_computation"
    REPORT_PREVIEW = "report_preview"
    PARAMETER_COMPARISON = "parameter_comparison"


def _require_utc_aware(timestamp: datetime) -> None:
    """Require an explicit UTC offset so audit timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise AccessGuardError("Access timestamps must be UTC-aware (offset 0).")


@dataclass(frozen=True)
class AccessDecision:
    """The decision on one test-set access request (SP 3.24).

    ``granted`` is true only for a final-evaluation access that the stage
    authorizes; ``reason`` explains a denial and is ``None`` when granted.
    """

    granted: bool
    reason: str | None

    def __post_init__(self) -> None:
        if self.granted and self.reason is not None:
            raise AccessGuardError("a granted access must not carry a reason.")
        if not self.granted and not self.reason:
            raise AccessGuardError("a denied access must carry a reason.")

    def readable(self) -> str:
        """Render the decision as one line."""
        if self.granted:
            return "granted"
        return f"denied: {self.reason}"


@dataclass(frozen=True)
class AccessAuditEntry:
    """One recorded test-set access attempt (audit event, SP 3.24).

    Records the access kind, the targeted test set, the stage at the attempt,
    whether it was granted, the denial reason (or ``None`` when granted) and
    the UTC request time, so every test-interval touch is auditable (SP 3.26).
    """

    access_kind: AccessKind
    test_set_id: str
    stage: ValidationStatus
    granted: bool
    reason: str | None
    requested_at: datetime

    def __post_init__(self) -> None:
        if not self.test_set_id:
            raise AccessGuardError("test_set_id must be non-empty.")
        _require_utc_aware(self.requested_at)
        if self.granted and self.reason is not None:
            raise AccessGuardError("a granted access must not carry a reason.")
        if not self.granted and not self.reason:
            raise AccessGuardError("a denied access must carry a reason.")

    def readable(self) -> str:
        """Render the audit entry as one line."""
        outcome = "granted" if self.granted else f"denied: {self.reason}"
        return (
            f"{self.access_kind.value} {self.test_set_id} at "
            f"{self.stage.value} {self.requested_at.isoformat()} [{outcome}]"
        )


def decide_access(
    access_kind: AccessKind,
    *,
    registration: HoldoutRegistration | None,
    current_stage: ValidationStatus,
) -> AccessDecision:
    """Decide whether a test-set access request is permitted (SP 3.24).

    Parameter comparison is always denied — selection never uses the test set
    (SP 3.21). The other kinds are granted only when a holdout is registered
    (SP 3.5) and the stage authorizes the test interval (SP 3.13
    ``is_test_authorized``, i.e. ``TEST_LOCKED``/``EVALUATED``); anything
    before ``TEST_LOCKED`` is denied.
    """
    if access_kind is AccessKind.PARAMETER_COMPARISON:
        return AccessDecision(
            granted=False,
            reason=(
                f"parameter comparison cannot use the test set at stage "
                f"{current_stage.value}; selection is restricted to training "
                "and validation data."
            ),
        )
    if registration is None:
        return AccessDecision(
            granted=False,
            reason=(f"{access_kind.value} cannot proceed: no test set is registered."),
        )
    if not is_test_authorized(current_stage):
        return AccessDecision(
            granted=False,
            reason=(
                f"{access_kind.value} of test set {registration.test_set_id} is "
                f"not authorized at stage {current_stage.value}; the test "
                "interval is only readable from TEST_LOCKED."
            ),
        )
    return AccessDecision(granted=True, reason=None)


@dataclass(frozen=True)
class AccessGuard:
    """Immutable controller that guards and audits test-set access (SP 3.24).

    ``registration`` is the SP 3.5 holdout (``None`` until registered);
    ``audit`` is the append-only trail of every access attempt. Each
    :meth:`authorize` returns a NEW guard with the attempt recorded plus the
    decision; :meth:`require` raises :class:`AccessGuardError` on a denial.
    """

    registration: HoldoutRegistration | None = None
    audit: tuple[AccessAuditEntry, ...] = ()

    def authorize(
        self,
        access_kind: AccessKind,
        *,
        current_stage: ValidationStatus,
        requested_at: datetime | None = None,
    ) -> tuple["AccessGuard", AccessDecision]:
        """Record and decide one test-set access attempt (non-raising).

        Returns the new guard (audit entry appended) and the decision, so a
        caller can proceed on ``granted`` without catching an exception.
        """
        decision = decide_access(
            access_kind,
            registration=self.registration,
            current_stage=current_stage,
        )
        timestamp = requested_at if requested_at is not None else datetime.now(timezone.utc)
        test_set_id = (
            self.registration.test_set_id if self.registration is not None else "unregistered"
        )
        entry = AccessAuditEntry(
            access_kind=access_kind,
            test_set_id=test_set_id,
            stage=current_stage,
            granted=decision.granted,
            reason=decision.reason,
            requested_at=timestamp,
        )
        return replace(self, audit=self.audit + (entry,)), decision

    def require(
        self,
        access_kind: AccessKind,
        *,
        current_stage: ValidationStatus,
        requested_at: datetime | None = None,
    ) -> "AccessGuard":
        """Require a test-set access, recording it and raising when denied.

        Returns the new guard with the attempt recorded. Raises
        :class:`AccessGuardError` with the denial reason when the stage is not
        authorized (or the access is parameter comparison).
        """
        new_guard, decision = self.authorize(
            access_kind,
            current_stage=current_stage,
            requested_at=requested_at,
        )
        if not decision.granted:
            raise AccessGuardError(decision.reason or "test-set access denied.")
        return new_guard

    def readable(self) -> str:
        """Render the guard state as one line."""
        test_set = (
            f"set {self.registration.test_set_id}" if self.registration is not None else "none"
        )
        return f"test access guard: {test_set}, {len(self.audit)} access attempts"


def access_audit_json(guard: AccessGuard) -> str:
    """Return a stable, key-sorted JSON serialization of the guard audit.

    The audit trail — every access attempt with its kind, test set, stage,
    outcome, reason and UTC time — serializes canonically so equal guard
    histories replay identically (SP 3.26 / 3.28).
    """
    payload: dict[str, object] = {
        "registration": (
            {
                "test_set_id": guard.registration.test_set_id,
                "purpose": guard.registration.purpose.value,
                "authorized_stage": guard.registration.authorized_stage.value,
                "config_hash": guard.registration.config_hash,
            }
            if guard.registration is not None
            else None
        ),
        "audit": [
            {
                "access_kind": entry.access_kind.value,
                "test_set_id": entry.test_set_id,
                "stage": entry.stage.value,
                "granted": entry.granted,
                "reason": entry.reason,
                "requested_at": entry.requested_at.isoformat(),
            }
            for entry in guard.audit
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def access_audit_fingerprint(guard: AccessGuard) -> str:
    """Return the stable SHA-256 fingerprint of a guard's audit trail (SP 3.24)."""
    return hashlib.sha256(access_audit_json(guard).encode("utf-8")).hexdigest()
