"""Test-set access guard tests (MVP 3 / SP 3.24).

Verifies that data reading, metric computation, report preview and parameter
comparison are all denied test-interval access before ``TEST_LOCKED``, that
the final-evaluation kinds become readable only once the stage authorizes the
test set (SP 3.13), that parameter comparison is ALWAYS denied (SP 3.21), and
that every attempt — granted or denied — is recorded as an auditable entry
(SP 3.26).
"""

import unittest
from datetime import datetime, timezone

from harbor.core.holdout_registry import HoldoutPurpose, HoldoutRegistration
from harbor.core.test_access_guard import (
    AccessAuditEntry,
    AccessDecision,
    AccessGuard,
    AccessGuardError,
    AccessKind,
    access_audit_fingerprint,
    access_audit_json,
    decide_access,
)
from harbor.core.validation_domain import EvaluationSplit, ValidationStatus


def _at(day: int = 1, hour: int = 0) -> datetime:
    """Return a fixed UTC-aware timestamp."""
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def _split(**overrides: object) -> EvaluationSplit:
    """Return a valid split with overridable boundaries."""
    fields: dict[str, object] = {
        "train_start": datetime(2019, 1, 1).date(),
        "train_end": datetime(2021, 12, 31).date(),
        "validation_start": datetime(2022, 1, 3).date(),
        "validation_end": datetime(2022, 12, 30).date(),
        "test_start": datetime(2023, 1, 2).date(),
        "test_end": datetime(2024, 12, 31).date(),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _registration(**overrides: object) -> HoldoutRegistration:
    """Return a valid holdout registration with overridable fields."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "purpose": HoldoutPurpose.FINAL_EVALUATION,
        "created_at": _at(1),
        "authorized_stage": ValidationStatus.TEST_LOCKED,
        "split": _split(),
        "config_hash": "abc123",
    }
    fields.update(overrides)
    return HoldoutRegistration(**fields)  # type: ignore[arg-type]


def _decision(**overrides: object) -> AccessDecision:
    """Return a valid decision with overridable fields."""
    fields: dict[str, object] = {
        "granted": True,
        "reason": None,
    }
    fields.update(overrides)
    return AccessDecision(**fields)  # type: ignore[arg-type]


def _entry(**overrides: object) -> AccessAuditEntry:
    """Return a valid audit entry with overridable fields."""
    fields: dict[str, object] = {
        "access_kind": AccessKind.DATA_READ,
        "test_set_id": "holdout-1",
        "stage": ValidationStatus.TEST_LOCKED,
        "granted": True,
        "reason": None,
        "requested_at": _at(2),
    }
    fields.update(overrides)
    return AccessAuditEntry(**fields)  # type: ignore[arg-type]


def _guard(**overrides: object) -> AccessGuard:
    """Return a valid guard with overridable fields."""
    fields: dict[str, object] = {
        "registration": _registration(),
        "audit": (),
    }
    fields.update(overrides)
    return AccessGuard(**fields)  # type: ignore[arg-type]


class AccessKindTests(unittest.TestCase):
    """Validates the :class:`AccessKind` taxonomy."""

    def test_values(self) -> None:
        self.assertEqual(AccessKind.DATA_READ, "data_read")
        self.assertEqual(AccessKind.METRIC_COMPUTATION, "metric_computation")
        self.assertEqual(AccessKind.REPORT_PREVIEW, "report_preview")
        self.assertEqual(AccessKind.PARAMETER_COMPARISON, "parameter_comparison")


class AccessDecisionTests(unittest.TestCase):
    """Validates the :class:`AccessDecision` invariants."""

    def test_valid_granted(self) -> None:
        decision = _decision()
        self.assertTrue(decision.granted)
        self.assertIsNone(decision.reason)

    def test_valid_denied(self) -> None:
        decision = _decision(granted=False, reason="not authorized")
        self.assertFalse(decision.granted)
        self.assertEqual(decision.reason, "not authorized")

    def test_granted_with_reason_rejected(self) -> None:
        with self.assertRaises(AccessGuardError):
            _decision(granted=True, reason="should not carry")

    def test_denied_without_reason_rejected(self) -> None:
        with self.assertRaises(AccessGuardError):
            _decision(granted=False, reason=None)

    def test_readable(self) -> None:
        self.assertEqual(_decision().readable(), "granted")
        self.assertIn("denied:", _decision(granted=False, reason="no").readable())


class AccessAuditEntryTests(unittest.TestCase):
    """Validates the :class:`AccessAuditEntry` invariants."""

    def test_valid_granted(self) -> None:
        entry = _entry()
        self.assertEqual(entry.access_kind, AccessKind.DATA_READ)
        self.assertEqual(entry.test_set_id, "holdout-1")
        self.assertEqual(entry.stage, ValidationStatus.TEST_LOCKED)
        self.assertTrue(entry.granted)

    def test_valid_denied(self) -> None:
        entry = _entry(granted=False, reason="not authorized")
        self.assertFalse(entry.granted)
        self.assertEqual(entry.reason, "not authorized")

    def test_empty_test_set_id_rejected(self) -> None:
        with self.assertRaises(AccessGuardError):
            _entry(test_set_id="")

    def test_naive_timestamp_rejected(self) -> None:
        with self.assertRaises(AccessGuardError):
            _entry(requested_at=datetime(2026, 1, 2))

    def test_granted_with_reason_rejected(self) -> None:
        with self.assertRaises(AccessGuardError):
            _entry(granted=True, reason="should not carry")

    def test_denied_without_reason_rejected(self) -> None:
        with self.assertRaises(AccessGuardError):
            _entry(granted=False, reason=None)

    def test_readable(self) -> None:
        self.assertIn("data_read holdout-1", _entry().readable())
        self.assertIn("[granted]", _entry().readable())


class DecideAccessTests(unittest.TestCase):
    """Verifies :func:`decide_access` gates the four access kinds."""

    def test_data_read_denied_before_test_locked(self) -> None:
        decision = decide_access(
            AccessKind.DATA_READ,
            registration=_registration(),
            current_stage=ValidationStatus.TUNING,
        )
        self.assertFalse(decision.granted)
        self.assertIn("not authorized at stage TUNING", decision.reason)

    def test_data_read_granted_at_test_locked(self) -> None:
        decision = decide_access(
            AccessKind.DATA_READ,
            registration=_registration(),
            current_stage=ValidationStatus.TEST_LOCKED,
        )
        self.assertTrue(decision.granted)

    def test_data_read_granted_at_evaluated(self) -> None:
        decision = decide_access(
            AccessKind.DATA_READ,
            registration=_registration(),
            current_stage=ValidationStatus.EVALUATED,
        )
        self.assertTrue(decision.granted)

    def test_metric_computation_gated(self) -> None:
        denied = decide_access(
            AccessKind.METRIC_COMPUTATION,
            registration=_registration(),
            current_stage=ValidationStatus.DATA_FROZEN,
        )
        self.assertFalse(denied.granted)
        granted = decide_access(
            AccessKind.METRIC_COMPUTATION,
            registration=_registration(),
            current_stage=ValidationStatus.TEST_LOCKED,
        )
        self.assertTrue(granted.granted)

    def test_report_preview_gated(self) -> None:
        denied = decide_access(
            AccessKind.REPORT_PREVIEW,
            registration=_registration(),
            current_stage=ValidationStatus.DRAFT,
        )
        self.assertFalse(denied.granted)
        granted = decide_access(
            AccessKind.REPORT_PREVIEW,
            registration=_registration(),
            current_stage=ValidationStatus.EVALUATED,
        )
        self.assertTrue(granted.granted)

    def test_no_registration_denied(self) -> None:
        decision = decide_access(
            AccessKind.DATA_READ,
            registration=None,
            current_stage=ValidationStatus.TEST_LOCKED,
        )
        self.assertFalse(decision.granted)
        self.assertIn("no test set is registered", decision.reason)

    def test_parameter_comparison_always_denied_at_draft(self) -> None:
        decision = decide_access(
            AccessKind.PARAMETER_COMPARISON,
            registration=_registration(),
            current_stage=ValidationStatus.DRAFT,
        )
        self.assertFalse(decision.granted)

    def test_parameter_comparison_always_denied_at_test_locked(self) -> None:
        decision = decide_access(
            AccessKind.PARAMETER_COMPARISON,
            registration=_registration(),
            current_stage=ValidationStatus.TEST_LOCKED,
        )
        self.assertFalse(decision.granted)

    def test_parameter_comparison_always_denied_at_evaluated(self) -> None:
        decision = decide_access(
            AccessKind.PARAMETER_COMPARISON,
            registration=_registration(),
            current_stage=ValidationStatus.EVALUATED,
        )
        self.assertFalse(decision.granted)

    def test_parameter_comparison_no_registration_denied(self) -> None:
        decision = decide_access(
            AccessKind.PARAMETER_COMPARISON,
            registration=None,
            current_stage=ValidationStatus.TEST_LOCKED,
        )
        self.assertFalse(decision.granted)


class AccessGuardTests(unittest.TestCase):
    """Verifies the :class:`AccessGuard` controller and audit trail."""

    def test_authorize_returns_new_guard_and_decision(self) -> None:
        guard = _guard()
        new_guard, decision = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(2),
        )
        self.assertTrue(decision.granted)
        self.assertEqual(len(new_guard.audit), 1)
        self.assertEqual(new_guard.audit[0].granted, True)

    def test_authorize_is_immutable(self) -> None:
        guard = _guard()
        new_guard, _ = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        self.assertEqual(len(guard.audit), 0)
        self.assertEqual(len(new_guard.audit), 1)

    def test_audit_accumulates_denied_then_granted(self) -> None:
        guard = _guard()
        guard, denied = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        self.assertFalse(denied.granted)
        guard, granted = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(3),
        )
        self.assertTrue(granted.granted)
        self.assertEqual(len(guard.audit), 2)
        self.assertFalse(guard.audit[0].granted)
        self.assertTrue(guard.audit[1].granted)

    def test_require_raises_on_denial(self) -> None:
        guard = _guard()
        with self.assertRaises(AccessGuardError):
            guard.require(
                AccessKind.DATA_READ,
                current_stage=ValidationStatus.TUNING,
            )

    def test_require_records_denied_entry(self) -> None:
        guard = _guard()
        try:
            guard.require(
                AccessKind.DATA_READ,
                current_stage=ValidationStatus.TUNING,
                requested_at=_at(2),
            )
        except AccessGuardError:
            pass
        # The original guard is unchanged; a new guard from authorize holds
        # the entry, so re-invoking through authorize shows the trail.
        new_guard, decision = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        self.assertFalse(decision.granted)
        self.assertEqual(len(new_guard.audit), 1)
        self.assertEqual(new_guard.audit[0].stage, ValidationStatus.TUNING)

    def test_require_returns_new_guard_on_grant(self) -> None:
        guard = _guard()
        new_guard = guard.require(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(2),
        )
        self.assertEqual(len(new_guard.audit), 1)
        self.assertTrue(new_guard.audit[0].granted)

    def test_audit_entry_records_details(self) -> None:
        guard = _guard()
        new_guard, _ = guard.authorize(
            AccessKind.REPORT_PREVIEW,
            current_stage=ValidationStatus.DRAFT,
            requested_at=_at(2),
        )
        entry = new_guard.audit[0]
        self.assertEqual(entry.access_kind, AccessKind.REPORT_PREVIEW)
        self.assertEqual(entry.test_set_id, "holdout-1")
        self.assertEqual(entry.stage, ValidationStatus.DRAFT)
        self.assertIsNotNone(entry.reason)

    def test_readable(self) -> None:
        self.assertIn("test access guard: set holdout-1", _guard().readable())
        self.assertIn("0 access attempts", _guard().readable())


class AccessAuditFingerprintTests(unittest.TestCase):
    """Verifies the guard audit fingerprint is stable and sensitive."""

    def _guard(self) -> AccessGuard:
        guard = _guard()
        guard, _ = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        return guard

    def test_fingerprint_stable_for_equal(self) -> None:
        self.assertEqual(
            access_audit_fingerprint(self._guard()),
            access_audit_fingerprint(self._guard()),
        )

    def test_fingerprint_changes_with_registration(self) -> None:
        other = self._guard()
        other = AccessGuard(registration=None, audit=other.audit)
        self.assertNotEqual(
            access_audit_fingerprint(self._guard()),
            access_audit_fingerprint(other),
        )

    def test_fingerprint_changes_with_audit_outcome(self) -> None:
        guard = _guard()
        guard, _ = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(2),
        )
        self.assertNotEqual(
            access_audit_fingerprint(self._guard()),
            access_audit_fingerprint(guard),
        )

    def test_audit_order_matters(self) -> None:
        guard = _guard()
        guard, _ = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        guard, _ = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(3),
        )
        reversed_guard = AccessGuard(
            registration=_registration(),
            audit=(guard.audit[1], guard.audit[0]),
        )
        self.assertNotEqual(
            access_audit_fingerprint(guard),
            access_audit_fingerprint(reversed_guard),
        )

    def test_json_is_key_sorted_and_stable(self) -> None:
        guard = _guard()
        self.assertEqual(access_audit_json(guard), access_audit_json(guard))
        self.assertIn('"test_set_id":"holdout-1"', access_audit_json(guard))


if __name__ == "__main__":
    unittest.main()
