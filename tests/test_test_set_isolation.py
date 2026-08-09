"""Test-set isolation tests (MVP 3 / SP 3.26).

The test interval is isolated: a validation run must never select parameters
using test-period metrics, read test market data, or compare trials by test
performance before ``TEST_LOCKED``. These tests deliberately attempt all three
misuses (故意尝试) and confirm every one is rejected AND produces an auditable
event (确认全部被拒绝并产生审计事件): the SP 3.24
:class:`~harbor.core.test_access_guard.AccessGuard` returns a denied
:class:`~harbor.core.test_access_guard.AccessDecision` (or raises
:class:`~harbor.core.test_access_guard.AccessGuardError` via ``require``) and
appends a UTC-stamped
:class:`~harbor.core.test_access_guard.AccessAuditEntry` to its audit trail,
which is fingerprinted for replayability (SP 3.28).
"""

import unittest
from datetime import datetime, timezone

from harbor.core.holdout_registry import HoldoutPurpose, HoldoutRegistration
from harbor.core.test_access_guard import (
    AccessAuditEntry,
    AccessGuard,
    AccessGuardError,
    AccessKind,
    access_audit_fingerprint,
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
    """Return a registered holdout (SP 3.5) with overridable fields."""
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


def _guard(**overrides: object) -> AccessGuard:
    """Return a guard over the registered holdout with overridable fields."""
    fields: dict[str, object] = {
        "registration": _registration(),
        "audit": (),
    }
    fields.update(overrides)
    return AccessGuard(**fields)  # type: ignore[arg-type]


class ParameterSelectionIsolationTests(unittest.TestCase):
    """Deliberately selects parameters with test-period metrics (用测试期指标选参)."""

    def test_parameter_selection_denied_before_test_locked(self) -> None:
        guard = _guard()
        new_guard, decision = guard.authorize(
            AccessKind.PARAMETER_COMPARISON,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        self.assertFalse(decision.granted)
        self.assertIn("parameter comparison", decision.reason)
        self.assertEqual(len(new_guard.audit), 1)

    def test_parameter_selection_denied_even_after_test_locked(self) -> None:
        # Even once the test set is locked for final evaluation, parameter
        # selection never uses it (SP 3.21 / 3.24).
        guard = _guard()
        new_guard, decision = guard.authorize(
            AccessKind.PARAMETER_COMPARISON,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(2),
        )
        self.assertFalse(decision.granted)
        self.assertIn("selection is restricted", decision.reason)
        self.assertEqual(len(new_guard.audit), 1)

    def test_parameter_selection_denied_at_evaluated(self) -> None:
        guard = _guard()
        _, decision = guard.authorize(
            AccessKind.PARAMETER_COMPARISON,
            current_stage=ValidationStatus.EVALUATED,
            requested_at=_at(2),
        )
        self.assertFalse(decision.granted)

    def test_parameter_selection_denied_at_every_stage(self) -> None:
        guard = _guard()
        for stage in ValidationStatus:
            guard, decision = guard.authorize(
                AccessKind.PARAMETER_COMPARISON,
                current_stage=stage,
                requested_at=_at(2),
            )
            self.assertFalse(decision.granted, f"stage {stage.value} must deny")
        self.assertEqual(len(guard.audit), len(ValidationStatus))

    def test_parameter_selection_require_raises_and_audits(self) -> None:
        guard = _guard()
        with self.assertRaises(AccessGuardError):
            guard.require(
                AccessKind.PARAMETER_COMPARISON,
                current_stage=ValidationStatus.TEST_LOCKED,
                requested_at=_at(2),
            )
        # The original guard is unchanged; authorize records the denial.
        new_guard, _ = guard.authorize(
            AccessKind.PARAMETER_COMPARISON,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(2),
        )
        self.assertEqual(len(new_guard.audit), 1)
        self.assertFalse(new_guard.audit[0].granted)


class TestDataReadIsolationTests(unittest.TestCase):
    """Deliberately reads test market data (读取测试行情)."""

    def test_test_data_read_denied_before_test_locked(self) -> None:
        guard = _guard()
        new_guard, decision = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        self.assertFalse(decision.granted)
        self.assertIn("not authorized at stage TUNING", decision.reason)
        self.assertEqual(len(new_guard.audit), 1)

    def test_test_data_read_denied_at_data_frozen(self) -> None:
        guard = _guard()
        new_guard, decision = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.DATA_FROZEN,
            requested_at=_at(2),
        )
        self.assertFalse(decision.granted)
        self.assertEqual(len(new_guard.audit), 1)

    def test_test_data_read_denied_at_draft(self) -> None:
        guard = _guard()
        _, decision = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.DRAFT,
            requested_at=_at(2),
        )
        self.assertFalse(decision.granted)

    def test_test_data_read_granted_only_at_test_locked(self) -> None:
        guard = _guard()
        guard, decision = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(2),
        )
        self.assertTrue(decision.granted)
        # The granted final-evaluation read is also audited.
        self.assertEqual(len(guard.audit), 1)
        self.assertTrue(guard.audit[0].granted)

    def test_test_data_read_without_registration_denied(self) -> None:
        guard = AccessGuard(registration=None)
        _, decision = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(2),
        )
        self.assertFalse(decision.granted)
        self.assertIn("no test set is registered", decision.reason)


class TrialComparisonIsolationTests(unittest.TestCase):
    """Deliberately compares trials by test performance (比较试验)."""

    def test_trial_comparison_denied_before_test_locked(self) -> None:
        guard = _guard()
        new_guard, decision = guard.authorize(
            AccessKind.PARAMETER_COMPARISON,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        self.assertFalse(decision.granted)
        self.assertEqual(len(new_guard.audit), 1)

    def test_trial_comparison_never_granted(self) -> None:
        guard = _guard()
        for stage in (
            ValidationStatus.DRAFT,
            ValidationStatus.TEST_LOCKED,
            ValidationStatus.EVALUATED,
        ):
            guard, decision = guard.authorize(
                AccessKind.PARAMETER_COMPARISON,
                current_stage=stage,
                requested_at=_at(2),
            )
            self.assertFalse(decision.granted)

    def test_comparison_denial_is_audited(self) -> None:
        guard = _guard()
        guard, _ = guard.authorize(
            AccessKind.PARAMETER_COMPARISON,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(2),
        )
        self.assertEqual(guard.audit[0].access_kind, AccessKind.PARAMETER_COMPARISON)
        self.assertEqual(guard.audit[0].test_set_id, "holdout-1")
        self.assertIsNotNone(guard.audit[0].reason)


class AuditEventTests(unittest.TestCase):
    """Verifies every rejected misuse produces an auditable event (产生审计事件)."""

    def test_each_attempt_appends_audit_entry(self) -> None:
        guard = _guard()
        for kind in (
            AccessKind.PARAMETER_COMPARISON,
            AccessKind.DATA_READ,
            AccessKind.METRIC_COMPUTATION,
            AccessKind.REPORT_PREVIEW,
        ):
            guard, decision = guard.authorize(
                kind,
                current_stage=ValidationStatus.TUNING,
                requested_at=_at(2),
            )
            self.assertFalse(decision.granted)
        self.assertEqual(len(guard.audit), 4)
        self.assertTrue(all(not entry.granted for entry in guard.audit))

    def test_audit_entries_record_details(self) -> None:
        guard = _guard()
        guard, _ = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        entry = guard.audit[0]
        self.assertIsInstance(entry, AccessAuditEntry)
        self.assertEqual(entry.access_kind, AccessKind.DATA_READ)
        self.assertEqual(entry.test_set_id, "holdout-1")
        self.assertEqual(entry.stage, ValidationStatus.TUNING)
        self.assertFalse(entry.granted)
        self.assertIsNotNone(entry.reason)
        self.assertEqual(entry.requested_at, _at(2))

    def test_audit_is_immutable(self) -> None:
        guard = _guard()
        new_guard, _ = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        self.assertEqual(len(guard.audit), 0)
        self.assertEqual(len(new_guard.audit), 1)

    def test_denied_then_granted_recorded_in_order(self) -> None:
        guard = _guard()
        guard, denied = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        guard, granted = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TEST_LOCKED,
            requested_at=_at(3),
        )
        self.assertFalse(denied.granted)
        self.assertTrue(granted.granted)
        self.assertEqual([entry.granted for entry in guard.audit], [False, True])

    def test_audit_fingerprint_changes_with_each_attempt(self) -> None:
        guard = _guard()
        guard, _ = guard.authorize(
            AccessKind.PARAMETER_COMPARISON,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        first = access_audit_fingerprint(guard)
        guard, _ = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=ValidationStatus.TUNING,
            requested_at=_at(2),
        )
        self.assertNotEqual(first, access_audit_fingerprint(guard))

    def test_all_misuses_rejected_and_audited(self) -> None:
        # The acceptance: 故意尝试用测试期指标选择参数、读取测试行情和比较试验,
        # 确认全部被拒绝并产生审计事件.
        guard = _guard()
        attempts = (
            (AccessKind.PARAMETER_COMPARISON, ValidationStatus.TUNING),
            (AccessKind.DATA_READ, ValidationStatus.TUNING),
            (AccessKind.PARAMETER_COMPARISON, ValidationStatus.TEST_LOCKED),
        )
        for kind, stage in attempts:
            guard, decision = guard.authorize(kind, current_stage=stage, requested_at=_at(2))
            self.assertFalse(decision.granted)
        self.assertEqual(len(guard.audit), len(attempts))
        self.assertTrue(all(not entry.granted for entry in guard.audit))
        self.assertEqual(len(guard.audit), 3)


if __name__ == "__main__":
    unittest.main()
