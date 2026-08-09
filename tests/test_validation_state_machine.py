"""Auditable validation state machine tests (MVP 3 / SP 3.13).

Verifies the lifecycle transitions (DRAFT -> DATA_FROZEN -> TUNING ->
TEST_LOCKED -> EVALUATED, with NOT_QUALIFIED / FAILED as terminal states),
the test-set access guard, the immutable audit trail and that a failed or
not-qualified run retains its diagnostics (warnings, error summary, stage and
reason).
"""

import unittest
from datetime import datetime, timedelta, timezone

from harbor.core.validation_domain import ValidationStatus
from harbor.core.validation_state_machine import (
    ValidationDiagnostics,
    ValidationRunState,
    ValidationStateError,
    ValidationTransition,
    allowed_transitions,
    can_transition,
    is_test_authorized,
    require_test_authorized,
    validation_initial_state,
)


def _at(hour: int = 12) -> datetime:
    """Return a fixed UTC-aware timestamp at the given hour (deterministic)."""
    return datetime(2026, 8, 9, hour, 0, 0, tzinfo=timezone.utc)


def _offset_at(hours: int) -> datetime:
    """Return a fixed timestamp with a non-zero UTC offset."""
    return datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone(timedelta(hours=hours)))


class TransitionTests(unittest.TestCase):
    """Verify valid and invalid lifecycle transitions."""

    def test_initial_state_is_draft(self) -> None:
        state = validation_initial_state("validation-1")
        self.assertEqual(state.status, ValidationStatus.DRAFT)
        self.assertEqual(state.run_id, "validation-1")
        self.assertEqual(state.transitions, ())

    def test_freeze_moves_to_data_frozen(self) -> None:
        state = validation_initial_state("validation-1").freeze()
        self.assertEqual(state.status, ValidationStatus.DATA_FROZEN)

    def test_tune_moves_to_tuning(self) -> None:
        state = validation_initial_state("validation-1").freeze().tune()
        self.assertEqual(state.status, ValidationStatus.TUNING)

    def test_lock_test_set_directly_from_data_frozen(self) -> None:
        # A pre-registered baseline (SP 3.30) locks the test set without tuning.
        state = validation_initial_state("validation-1").freeze().lock_test_set()
        self.assertEqual(state.status, ValidationStatus.TEST_LOCKED)

    def test_lock_test_set_after_tuning(self) -> None:
        state = validation_initial_state("validation-1").freeze().tune().lock_test_set()
        self.assertEqual(state.status, ValidationStatus.TEST_LOCKED)

    def test_evaluate_from_test_locked(self) -> None:
        state = validation_initial_state("validation-1").freeze().lock_test_set().evaluate()
        self.assertEqual(state.status, ValidationStatus.EVALUATED)

    def test_full_lifecycle_with_tuning(self) -> None:
        state = validation_initial_state("validation-1").freeze().tune().lock_test_set().evaluate()
        self.assertEqual(state.status, ValidationStatus.EVALUATED)

    def test_data_frozen_can_be_not_qualified(self) -> None:
        state = (
            validation_initial_state("validation-1")
            .freeze()
            .mark_not_qualified(reason="price coverage below the 95% threshold")
        )
        self.assertEqual(state.status, ValidationStatus.NOT_QUALIFIED)

    def test_tuning_can_be_not_qualified(self) -> None:
        state = (
            validation_initial_state("validation-1")
            .freeze()
            .tune()
            .mark_not_qualified(reason="no parameter trial passed the risk constraints")
        )
        self.assertEqual(state.status, ValidationStatus.NOT_QUALIFIED)

    def test_test_locked_can_be_not_qualified(self) -> None:
        state = (
            validation_initial_state("validation-1")
            .freeze()
            .lock_test_set()
            .mark_not_qualified(reason="test leakage detected")
        )
        self.assertEqual(state.status, ValidationStatus.NOT_QUALIFIED)

    def test_evaluated_can_degrade_to_not_qualified(self) -> None:
        state = (
            validation_initial_state("validation-1")
            .freeze()
            .lock_test_set()
            .evaluate()
            .mark_not_qualified(reason="stability rules not satisfied")
        )
        self.assertEqual(state.status, ValidationStatus.NOT_QUALIFIED)

    def test_draft_cannot_tune_directly(self) -> None:
        with self.assertRaisesRegex(ValidationStateError, "DRAFT -> TUNING"):
            validation_initial_state("validation-1").tune()

    def test_draft_cannot_lock_test_set_directly(self) -> None:
        with self.assertRaisesRegex(ValidationStateError, "DRAFT -> TEST_LOCKED"):
            validation_initial_state("validation-1").lock_test_set()

    def test_draft_cannot_evaluate_directly(self) -> None:
        with self.assertRaisesRegex(ValidationStateError, "DRAFT -> EVALUATED"):
            validation_initial_state("validation-1").evaluate()

    def test_data_frozen_cannot_evaluate_directly(self) -> None:
        with self.assertRaisesRegex(ValidationStateError, "DATA_FROZEN -> EVALUATED"):
            validation_initial_state("validation-1").freeze().evaluate()

    def test_draft_can_fail(self) -> None:
        state = validation_initial_state("validation-1").fail("invalid config")
        self.assertEqual(state.status, ValidationStatus.FAILED)

    def test_active_states_can_fail(self) -> None:
        for builder in (
            lambda: validation_initial_state("v").freeze(),
            lambda: validation_initial_state("v").freeze().tune(),
            lambda: validation_initial_state("v").freeze().lock_test_set(),
        ):
            with self.subTest():
                self.assertEqual(builder().fail("boom").status, ValidationStatus.FAILED)

    def test_not_qualified_is_terminal(self) -> None:
        state = (
            validation_initial_state("validation-1")
            .freeze()
            .mark_not_qualified(reason="coverage gap")
        )
        with self.assertRaisesRegex(ValidationStateError, "NOT_QUALIFIED -> TUNING"):
            state.tune()

    def test_failed_cannot_resume(self) -> None:
        state = validation_initial_state("validation-1").freeze().fail("boom")
        with self.assertRaisesRegex(ValidationStateError, "FAILED -> DATA_FROZEN"):
            state.freeze()

    def test_can_transition_helper(self) -> None:
        self.assertTrue(can_transition(ValidationStatus.DRAFT, ValidationStatus.DATA_FROZEN))
        self.assertTrue(can_transition(ValidationStatus.TUNING, ValidationStatus.TEST_LOCKED))
        self.assertTrue(can_transition(ValidationStatus.EVALUATED, ValidationStatus.NOT_QUALIFIED))
        self.assertFalse(can_transition(ValidationStatus.NOT_QUALIFIED, ValidationStatus.TUNING))
        self.assertFalse(can_transition(ValidationStatus.FAILED, ValidationStatus.DATA_FROZEN))

    def test_allowed_transitions(self) -> None:
        self.assertEqual(
            allowed_transitions(ValidationStatus.DATA_FROZEN),
            frozenset(
                {
                    ValidationStatus.TUNING,
                    ValidationStatus.TEST_LOCKED,
                    ValidationStatus.NOT_QUALIFIED,
                    ValidationStatus.FAILED,
                }
            ),
        )
        self.assertEqual(allowed_transitions(ValidationStatus.FAILED), frozenset())


class TestAccessGuardTests(unittest.TestCase):
    """Verify the test-set access guard (SP 3.13 / 3.24)."""

    def test_test_authorized_after_lock_and_evaluated(self) -> None:
        self.assertTrue(is_test_authorized(ValidationStatus.TEST_LOCKED))
        self.assertTrue(is_test_authorized(ValidationStatus.EVALUATED))

    def test_test_not_authorized_before_lock(self) -> None:
        for status in (
            ValidationStatus.DRAFT,
            ValidationStatus.DATA_FROZEN,
            ValidationStatus.TUNING,
        ):
            with self.subTest(status=status.value):
                self.assertFalse(is_test_authorized(status))

    def test_require_test_authorized_allows_locked(self) -> None:
        require_test_authorized(ValidationStatus.TEST_LOCKED)
        require_test_authorized(ValidationStatus.EVALUATED)

    def test_require_test_authorized_rejects_early_stages(self) -> None:
        with self.assertRaisesRegex(ValidationStateError, "not readable at stage TUNING"):
            require_test_authorized(ValidationStatus.TUNING)

    def test_run_state_test_authorized_property(self) -> None:
        state = validation_initial_state("v").freeze()
        self.assertFalse(state.test_authorized)
        self.assertTrue(state.lock_test_set().test_authorized)


class AuditTrailTests(unittest.TestCase):
    """Verify every transition is recorded in the audit trail (SP 3.13)."""

    def test_transition_records_entry_with_default_reason(self) -> None:
        state = validation_initial_state("validation-1").freeze(recorded_at=_at())
        self.assertEqual(len(state.transitions), 1)
        entry = state.transitions[0]
        self.assertEqual(entry.from_status, ValidationStatus.DRAFT)
        self.assertEqual(entry.to_status, ValidationStatus.DATA_FROZEN)
        self.assertEqual(entry.reason, "dataset and split frozen")
        self.assertEqual(entry.recorded_at, _at())

    def test_transition_records_custom_reason(self) -> None:
        state = (
            validation_initial_state("validation-1")
            .freeze(reason="frozen at review meeting", recorded_at=_at())
            .tune(reason="budget 50 trials", recorded_at=_at(13))
        )
        self.assertEqual(len(state.transitions), 2)
        self.assertEqual(state.transitions[1].reason, "budget 50 trials")
        self.assertEqual(state.transitions[1].from_status, ValidationStatus.DATA_FROZEN)
        self.assertEqual(state.transitions[1].to_status, ValidationStatus.TUNING)

    def test_transition_returns_new_state(self) -> None:
        state = validation_initial_state("validation-1")
        frozen = state.freeze(recorded_at=_at())
        self.assertEqual(state.status, ValidationStatus.DRAFT)
        self.assertEqual(state.transitions, ())
        self.assertEqual(frozen.status, ValidationStatus.DATA_FROZEN)

    def test_transition_is_immutable(self) -> None:
        state = validation_initial_state("validation-1")
        state.freeze(recorded_at=_at())
        self.assertEqual(state.status, ValidationStatus.DRAFT)

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            validation_initial_state("v").freeze(recorded_at=datetime(2026, 8, 9, 12, 0, 0))

    def test_non_utc_offset_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            validation_initial_state("v").freeze(recorded_at=_offset_at(8))

    def test_validation_transition_rejects_naive_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            ValidationTransition(
                from_status=ValidationStatus.DRAFT,
                to_status=ValidationStatus.DATA_FROZEN,
                recorded_at=datetime(2026, 8, 9, 12, 0, 0),
            )

    def test_broken_chain_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "chain is broken"):
            ValidationRunState(
                run_id="v",
                status=ValidationStatus.TEST_LOCKED,
                transitions=(
                    ValidationTransition(
                        ValidationStatus.DRAFT,
                        ValidationStatus.DATA_FROZEN,
                        _at(),
                        "freeze",
                    ),
                    ValidationTransition(
                        ValidationStatus.TUNING,
                        ValidationStatus.TEST_LOCKED,
                        _at(13),
                        "skipped freeze",
                    ),
                ),
            )

    def test_final_transition_must_match_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "but the current status is"):
            ValidationRunState(
                run_id="v",
                status=ValidationStatus.TUNING,
                transitions=(
                    ValidationTransition(
                        ValidationStatus.DRAFT,
                        ValidationStatus.DATA_FROZEN,
                        _at(),
                        "freeze",
                    ),
                ),
            )

    def test_audit_trail_readable(self) -> None:
        state = validation_initial_state("validation-1").freeze(recorded_at=_at())
        summary = state.readable()
        self.assertIn("validation run validation-1: DATA_FROZEN", summary)
        self.assertIn("DRAFT -> DATA_FROZEN at", summary)


class DiagnosticsTests(unittest.TestCase):
    """Verify a failed or not-qualified run retains diagnostics (SP 3.13)."""

    def test_with_warning_accumulates(self) -> None:
        state = validation_initial_state("v").freeze().with_warning("calendar gap")
        self.assertIn("calendar gap", state.diagnostics.warnings)
        self.assertEqual(state.status, ValidationStatus.DATA_FROZEN)

    def test_fail_retains_warnings_error_and_stage(self) -> None:
        state = validation_initial_state("v").freeze().tune()
        state = state.with_warning("stale price").with_warning("missing fx")
        failed = state.fail("corporate action error", stage="fold_execution")
        self.assertEqual(failed.status, ValidationStatus.FAILED)
        self.assertEqual(failed.diagnostics.error_summary, "corporate action error")
        self.assertEqual(failed.diagnostics.stage, "fold_execution")
        self.assertEqual(failed.diagnostics.warnings, ("stale price", "missing fx"))

    def test_fail_requires_error_summary(self) -> None:
        with self.assertRaisesRegex(ValueError, "error_summary"):
            validation_initial_state("v").freeze().fail("")

    def test_fail_recorded_as_audit_reason(self) -> None:
        state = validation_initial_state("v").freeze().fail("boom")
        self.assertEqual(state.transitions[-1].to_status, ValidationStatus.FAILED)
        self.assertEqual(state.transitions[-1].reason, "boom")

    def test_mark_not_qualified_requires_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "reason"):
            validation_initial_state("v").freeze().mark_not_qualified(reason="")

    def test_mark_not_qualified_records_reason_in_diagnostics(self) -> None:
        state = (
            validation_initial_state("v")
            .freeze()
            .mark_not_qualified(reason="price coverage below the 95% threshold")
        )
        self.assertEqual(state.diagnostics.reason, "price coverage below the 95% threshold")
        self.assertEqual(state.transitions[-1].reason, "price coverage below the 95% threshold")

    def test_diagnostics_readable(self) -> None:
        diagnostics = ValidationDiagnostics(
            warnings=("w1",), error_summary="boom", stage="fold_execution", reason="coverage gap"
        )
        summary = diagnostics.readable()
        self.assertIn("stage: fold_execution", summary)
        self.assertIn("error: boom", summary)
        self.assertIn("reason: coverage gap", summary)
        self.assertIn("warning: w1", summary)


class ValidationRunStateValidationTests(unittest.TestCase):
    """Verify run state validation and rendering."""

    def test_empty_run_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "run_id"):
            ValidationRunState(run_id="", status=ValidationStatus.DRAFT)

    def test_with_warning_requires_message(self) -> None:
        with self.assertRaisesRegex(ValueError, "warning message"):
            validation_initial_state("v").with_warning("")

    def test_readable_lists_diagnostics_and_audit(self) -> None:
        state = validation_initial_state("v").freeze().tune()
        state = state.with_warning("calendar gap")
        summary = state.readable()
        self.assertIn("validation run v: TUNING", summary)
        self.assertIn("warning: calendar gap", summary)
        self.assertIn("DRAFT -> DATA_FROZEN", summary)
        self.assertIn("DATA_FROZEN -> TUNING", summary)


if __name__ == "__main__":
    unittest.main()
