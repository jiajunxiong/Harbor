"""Split & state-machine test suite (MVP 3 / SP 3.76).

Consolidated integration suite covering the five acceptance dimensions —
边界合法性 (boundary legality, SP 3.4), 测试集锁定 (test-set locking, SP 3.5 /
3.13), 合法状态迁移 (legal state transitions, SP 3.13), 数据漂移 (data drift,
SP 3.11) and 测试后变更政策 (post-test change policy, SP 3.42) — and the
INTERACTIONS between them: a frozen dataset whose fingerprint drifts refuses
reuse of an old conclusion, and a finalized holdout whose inputs change
requires a new test-set version and a new validation run (未通过不等于可调参
重测). No database is required.
"""

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market
from harbor.core.data_drift import DataDriftError, check_fingerprint, require_fingerprint_matches
from harbor.core.dataset_fingerprint import dataset_fingerprint
from harbor.core.dataset_manifest import build_dataset_manifest
from harbor.core.final_holdout import FinalHoldoutInputs, unlock_final_holdout
from harbor.core.holdout_registry import (
    HoldoutAccessError,
    HoldoutRegistrationError,
    guard_final_evaluation_read,
    mark_first_read,
    register_test_set,
)
from harbor.core.reaccess_policy import (
    ReaccessInputChange,
    ReaccessPolicyError,
    bump_test_set_version,
    check_test_reaccess,
    require_test_reaccess_compliance,
)
from harbor.core.validation_config import SplitConfig
from harbor.core.validation_domain import (
    DatasetManifest,
    EvaluationSplit,
    SplitBoundaryError,
    ValidationStatus,
)
from harbor.core.validation_split import (
    SPLIT_ORDER_RULE,
    check_split_boundaries,
    check_split_config,
    collect_split_boundary_issues,
    validate_split_boundaries,
    validate_split_config,
)
from harbor.core.validation_state_machine import (
    ValidationStateError,
    allowed_transitions,
    can_transition,
    is_test_authorized,
    validation_initial_state,
)

_TRAIN_START = date(2019, 1, 1)
_TRAIN_END = date(2020, 12, 31)
_VALIDATION_START = date(2021, 1, 1)
_VALIDATION_END = date(2021, 12, 31)
_TEST_START = date(2022, 1, 1)
_TEST_END = date(2022, 12, 31)

_CONFIG_HASH = "config-hash-demo"
_DATASET_FINGERPRINT = "dataset-fp-demo"
_SELECTION_FINGERPRINT = "selection-fp-demo"
_CODE_VERSION = "1.0.0"
_TEST_SET_ID = "holdout-1"
_CALENDAR_VERSION = "cal-1"


def _at(hour: int = 12) -> datetime:
    """Return a fixed UTC-aware timestamp at the given hour (deterministic)."""
    return datetime(2026, 8, 9, hour, 0, 0, tzinfo=timezone.utc)


def _split() -> EvaluationSplit:
    """Return the frozen valid split (SP 3.4)."""
    return validate_split_boundaries(
        _TRAIN_START,
        _TRAIN_END,
        _VALIDATION_START,
        _VALIDATION_END,
        _TEST_START,
        _TEST_END,
    )


def _manifest(**overrides) -> DatasetManifest:
    """Return a self-consistent frozen dataset manifest (SP 3.6/3.7)."""
    fields = dict(
        markets=(Market.HK,),
        base_currency=Currency.HKD,
        start_date=_TRAIN_START,
        end_date=_TEST_END,
        data_cutoff=_TEST_END,
        config_hash=_CONFIG_HASH,
        code_version=_CODE_VERSION,
        calendar_version=_CALENDAR_VERSION,
        fx_source="mock",
        fingerprint="unfingerprinted",
    )
    fields.update(overrides)
    manifest = build_dataset_manifest(**fields)  # type: ignore[arg-type]
    return replace(manifest, fingerprint=dataset_fingerprint(manifest))


def _registration(**overrides):
    """Register the independent holdout (SP 3.5) with a fixed creation time."""
    fields = dict(
        test_set_id=_TEST_SET_ID,
        split=_split(),
        config_hash=_CONFIG_HASH,
        created_at=_at(0),
    )
    fields.update(overrides)
    return register_test_set(**fields)  # type: ignore[arg-type]


def _inputs(**overrides) -> FinalHoldoutInputs:
    """Return the frozen final-evaluation inputs (SP 3.41)."""
    fields = dict(
        test_set_id=_TEST_SET_ID,
        dataset_fingerprint=_DATASET_FINGERPRINT,
        config_hash=_CONFIG_HASH,
        selection_fingerprint=_SELECTION_FINGERPRINT,
        code_version=_CODE_VERSION,
    )
    fields.update(overrides)
    return FinalHoldoutInputs(**fields)  # type: ignore[arg-type]


def _release():
    """Unlock the holdout once at EVALUATED and save the release (SP 3.41)."""
    return unlock_final_holdout(
        _registration(),
        current_stage=ValidationStatus.EVALUATED,
        responsibility="researcher",
        inputs=_inputs(),
        unlocked_at=_at(1),
    )


class SplitBoundaryLegalityTests(unittest.TestCase):
    """SP 3.4 边界合法性: valid splits accepted, every violation rejected."""

    def test_valid_split_returns_frozen_split(self) -> None:
        split = validate_split_boundaries(
            _TRAIN_START,
            _TRAIN_END,
            _VALIDATION_START,
            _VALIDATION_END,
            _TEST_START,
            _TEST_END,
        )
        self.assertGreater(split.train_days, 0)
        self.assertGreater(split.validation_days, 0)
        self.assertGreater(split.test_days, 0)

    def test_reversed_train_range_rejected(self) -> None:
        with self.assertRaises(SplitBoundaryError) as context:
            validate_split_boundaries(
                _TRAIN_START,
                date(2018, 12, 31),
                _VALIDATION_START,
                _VALIDATION_END,
                _TEST_START,
                _TEST_END,
            )
        self.assertIn(SPLIT_ORDER_RULE, str(context.exception))

    def test_overlapping_train_validation_rejected(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            validate_split_boundaries(
                _TRAIN_START,
                date(2021, 1, 1),
                _VALIDATION_START,
                _VALIDATION_END,
                _TEST_START,
                _TEST_END,
            )

    def test_touching_validation_test_rejected(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            validate_split_boundaries(
                _TRAIN_START,
                _TRAIN_END,
                _VALIDATION_START,
                _VALIDATION_END,
                _VALIDATION_END,
                date(2023, 12, 31),
            )

    def test_issues_enumerate_all_violations(self) -> None:
        # Reversed train AND overlapping validation/test -> both reported at once.
        issues = collect_split_boundary_issues(
            _TRAIN_START,
            date(2018, 12, 31),
            _VALIDATION_START,
            date(2023, 6, 30),
            date(2023, 1, 1),
            date(2023, 12, 31),
        )
        self.assertEqual(len(issues), 2)
        self.assertTrue(any("train range is empty or reversed" in issue for issue in issues))
        self.assertTrue(any("validation must end before test starts" in issue for issue in issues))

    def test_check_split_boundaries_is_non_raising(self) -> None:
        self.assertTrue(check_split_boundaries(*_split_boundaries()).valid)
        report = check_split_boundaries(
            _TRAIN_START,
            _TRAIN_END,
            _VALIDATION_START,
            _VALIDATION_END,
            _TEST_START,
            _TEST_END,
        )
        self.assertIn(SPLIT_ORDER_RULE, report.readable())

    def test_valid_split_config_validates(self) -> None:
        split = SplitConfig(
            train_start=_TRAIN_START,
            train_end=_TRAIN_END,
            validation_start=_VALIDATION_START,
            validation_end=_VALIDATION_END,
            test_start=_TEST_START,
            test_end=_TEST_END,
        )
        self.assertEqual(validate_split_config(split), split.to_evaluation_split())
        self.assertTrue(check_split_config(split).valid)

    def test_empty_test_range_reported(self) -> None:
        report = check_split_boundaries(
            _TRAIN_START,
            _TRAIN_END,
            _VALIDATION_START,
            _VALIDATION_END,
            _TEST_END,
            _TEST_START,
        )
        self.assertFalse(report.valid)
        self.assertTrue(any("test range" in issue for issue in report.issues))


def _split_boundaries() -> tuple[date, date, date, date, date, date]:
    """Return the canonical valid boundary tuple for helpers above."""
    return (
        _TRAIN_START,
        _TRAIN_END,
        _VALIDATION_START,
        _VALIDATION_END,
        _TEST_START,
        _TEST_END,
    )


class TestSetLockingTests(unittest.TestCase):
    """SP 3.5 / 3.13 测试集锁定: readable only from TEST_LOCKED, read once."""

    def test_registration_records_split_and_hash(self) -> None:
        registration = _registration()
        self.assertEqual(registration.test_set_id, _TEST_SET_ID)
        self.assertEqual(registration.split, _split())
        self.assertEqual(registration.config_hash, _CONFIG_HASH)
        self.assertEqual(registration.authorized_stage, ValidationStatus.TEST_LOCKED)

    def test_read_denied_before_test_locked(self) -> None:
        registration = _registration()
        for stage in (
            ValidationStatus.DRAFT,
            ValidationStatus.DATA_FROZEN,
            ValidationStatus.TUNING,
        ):
            with self.subTest(stage=stage.value):
                with self.assertRaises(HoldoutAccessError):
                    guard_final_evaluation_read(registration, stage)

    def test_read_allowed_at_test_locked_and_evaluated(self) -> None:
        registration = _registration()
        guard_final_evaluation_read(registration, ValidationStatus.TEST_LOCKED)
        guard_final_evaluation_read(registration, ValidationStatus.EVALUATED)

    def test_first_read_recorded_exactly_once(self) -> None:
        registration = _registration()
        updated = mark_first_read(registration, ValidationStatus.TEST_LOCKED, read_at=_at(1))
        self.assertEqual(updated.first_read_at, _at(1))
        self.assertIsNone(registration.first_read_at)
        with self.assertRaises(HoldoutRegistrationError):
            mark_first_read(updated, ValidationStatus.TEST_LOCKED, read_at=_at(2))

    def test_state_test_authorized_follows_lock(self) -> None:
        state = validation_initial_state("run-1")
        self.assertFalse(state.test_authorized)
        self.assertFalse(is_test_authorized(ValidationStatus.TUNING))
        locked = state.freeze().tune().lock_test_set()
        self.assertEqual(locked.status, ValidationStatus.TEST_LOCKED)
        self.assertTrue(locked.test_authorized)

    def test_lock_test_set_from_data_frozen_or_tuning(self) -> None:
        self.assertEqual(
            validation_initial_state("run-1").freeze().lock_test_set().status,
            ValidationStatus.TEST_LOCKED,
        )
        self.assertEqual(
            validation_initial_state("run-1").freeze().tune().lock_test_set().status,
            ValidationStatus.TEST_LOCKED,
        )


class StateMachineTransitionTests(unittest.TestCase):
    """SP 3.13 合法状态迁移: lifecycle transitions and audit trail."""

    def test_full_lifecycle(self) -> None:
        state = validation_initial_state("run-1").freeze().tune().lock_test_set().evaluate()
        self.assertEqual(state.status, ValidationStatus.EVALUATED)
        self.assertEqual(len(state.transitions), 4)

    def test_audit_trail_appends_entries(self) -> None:
        state = validation_initial_state("run-1").freeze().tune()
        self.assertEqual(state.transitions[0].from_status, ValidationStatus.DRAFT)
        self.assertEqual(state.transitions[0].to_status, ValidationStatus.DATA_FROZEN)
        self.assertEqual(state.transitions[1].from_status, ValidationStatus.DATA_FROZEN)
        self.assertEqual(state.transitions[1].to_status, ValidationStatus.TUNING)

    def test_illegal_transition_rejected(self) -> None:
        with self.assertRaises(ValidationStateError):
            validation_initial_state("run-1").tune()

    def test_terminal_states_are_frozen(self) -> None:
        not_qualified = (
            validation_initial_state("run-1").freeze().mark_not_qualified(reason="coverage gap")
        )
        self.assertEqual(not_qualified.status, ValidationStatus.NOT_QUALIFIED)
        with self.assertRaises(ValidationStateError):
            not_qualified.freeze()
        failed = validation_initial_state("run-1").fail("execution error")
        self.assertEqual(failed.status, ValidationStatus.FAILED)
        with self.assertRaises(ValidationStateError):
            failed.tune()

    def test_evaluated_can_degrade_to_not_qualified(self) -> None:
        state = validation_initial_state("run-1").freeze().tune().lock_test_set().evaluate()
        degraded = state.mark_not_qualified(reason="stress loss exceeds rule")
        self.assertEqual(degraded.status, ValidationStatus.NOT_QUALIFIED)
        self.assertEqual(degraded.diagnostics.reason, "stress loss exceeds rule")

    def test_allowed_transitions_matrix(self) -> None:
        self.assertEqual(
            allowed_transitions(ValidationStatus.DRAFT),
            frozenset({ValidationStatus.DATA_FROZEN, ValidationStatus.FAILED}),
        )
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
        self.assertEqual(
            allowed_transitions(ValidationStatus.EVALUATED),
            frozenset({ValidationStatus.NOT_QUALIFIED}),
        )
        self.assertTrue(can_transition(ValidationStatus.TEST_LOCKED, ValidationStatus.EVALUATED))
        self.assertFalse(can_transition(ValidationStatus.DRAFT, ValidationStatus.TUNING))


class DataDriftTests(unittest.TestCase):
    """SP 3.11 数据漂移: a changed frozen manifest refuses conclusion reuse."""

    def test_fingerprint_matches_when_unchanged(self) -> None:
        manifest = _manifest()
        result = check_fingerprint(manifest, dataset_fingerprint(manifest))
        self.assertFalse(result.drifted)
        self.assertIn("matches", result.readable())

    def test_changed_calendar_version_drifts(self) -> None:
        current = _manifest()
        changed = _manifest(calendar_version="cal-2")
        result = check_fingerprint(current, dataset_fingerprint(changed))
        self.assertTrue(result.drifted)
        self.assertIn("data drift", result.readable())

    def test_changed_data_cutoff_drifts(self) -> None:
        current = _manifest()
        changed = _manifest(data_cutoff=_TEST_START)
        self.assertTrue(check_fingerprint(current, dataset_fingerprint(changed)).drifted)

    def test_require_matches_passes(self) -> None:
        manifest = _manifest()
        require_fingerprint_matches(manifest, dataset_fingerprint(manifest))

    def test_require_matches_refuses_on_drift(self) -> None:
        current = _manifest()
        changed = _manifest(calendar_version="cal-2")
        with self.assertRaises(DataDriftError) as context:
            require_fingerprint_matches(
                current, dataset_fingerprint(changed), context="validation report"
            )
        self.assertIn("refusing to reuse validation report", str(context.exception))
        self.assertIn("data has drifted", str(context.exception))


class PostTestChangePolicyTests(unittest.TestCase):
    """SP 3.42 测试后变更政策: substantive change needs a new test set + run."""

    def setUp(self) -> None:
        self.release = _release()

    def test_unchanged_inputs_may_reuse(self) -> None:
        decision = check_test_reaccess(self.release, _inputs())
        self.assertEqual(decision.changes, ())
        self.assertFalse(decision.requires_new_test_set)
        self.assertFalse(decision.requires_new_validation_run)
        self.assertIsNone(decision.reason)

    def test_data_change_mandates_new_test_set_and_run(self) -> None:
        decision = check_test_reaccess(self.release, _inputs(dataset_fingerprint="dataset-fp-v2"))
        self.assertEqual(decision.changes, (ReaccessInputChange.DATA,))
        self.assertTrue(decision.requires_new_test_set)
        self.assertTrue(decision.requires_new_validation_run)
        self.assertIn("data", decision.reason or "")

    def test_each_change_dimension_detected(self) -> None:
        cases = (
            ("config_hash", ReaccessInputChange.STRATEGY),
            ("selection_fingerprint", ReaccessInputChange.PARAMETERS),
            ("code_version", ReaccessInputChange.CODE),
        )
        for field, change in cases:
            with self.subTest(field=field):
                decision = check_test_reaccess(
                    self.release,
                    _inputs(**{field: "changed"}),  # type: ignore[arg-type]
                )
                self.assertIn(change, decision.changes)

    def test_all_changes_in_canonical_order(self) -> None:
        decision = check_test_reaccess(
            self.release,
            _inputs(
                dataset_fingerprint="fp2",
                config_hash="cfg2",
                selection_fingerprint="sel2",
                code_version="2.0.0",
            ),
        )
        self.assertEqual(
            decision.changes,
            (
                ReaccessInputChange.DATA,
                ReaccessInputChange.STRATEGY,
                ReaccessInputChange.PARAMETERS,
                ReaccessInputChange.CODE,
            ),
        )

    def test_require_compliance_raises_on_finalized_reuse(self) -> None:
        with self.assertRaises(ReaccessPolicyError) as context:
            require_test_reaccess_compliance(self.release, _inputs(selection_fingerprint="sel2"))
        self.assertIn("parameters", str(context.exception))
        self.assertIn("SP 3.42", str(context.exception))

    def test_new_test_set_version_only_needs_new_run(self) -> None:
        current = _inputs(dataset_fingerprint="fp2", test_set_id="holdout-1-v2")
        decision = check_test_reaccess(self.release, current)
        self.assertEqual(decision.changes, (ReaccessInputChange.DATA,))
        self.assertFalse(decision.requires_new_test_set)
        self.assertTrue(decision.requires_new_validation_run)
        require_test_reaccess_compliance(self.release, current)

    def test_bump_test_set_version(self) -> None:
        self.assertEqual(bump_test_set_version("holdout-1"), "holdout-1-v2")
        self.assertEqual(bump_test_set_version("holdout-1-v2"), "holdout-1-v3")

    def test_decision_readable_names_changes(self) -> None:
        decision = check_test_reaccess(self.release, _inputs(code_version="2.0.0"))
        self.assertIn("code", decision.readable())
        self.assertIn("new test set required", decision.readable())


class EndToEndDisciplineTests(unittest.TestCase):
    """The guards compose: a drift or post-test change never reuses the old conclusion."""

    def _evaluated_state(self):
        return validation_initial_state("run-1").freeze().tune().lock_test_set().evaluate()

    def test_legal_path_reuses_conclusion(self) -> None:
        state = self._evaluated_state()
        release = _release()
        # Fingerprint matches AND inputs unchanged -> the old conclusion may be reused.
        require_fingerprint_matches(_manifest(), dataset_fingerprint(_manifest()))
        decision = require_test_reaccess_compliance(release, _inputs())
        self.assertEqual(decision.changes, ())
        self.assertEqual(state.status, ValidationStatus.EVALUATED)

    def test_data_drift_blocks_conclusion_reuse(self) -> None:
        _release()
        changed = _manifest(calendar_version="cal-2")
        with self.assertRaises(DataDriftError):
            require_fingerprint_matches(_manifest(), dataset_fingerprint(changed))

    def test_post_test_change_blocks_conclusion_reuse(self) -> None:
        release = _release()
        with self.assertRaises(ReaccessPolicyError):
            require_test_reaccess_compliance(release, _inputs(selection_fingerprint="sel2"))
        # The mandated path: bump the test-set version and start a new run.
        self.assertEqual(bump_test_set_version(_TEST_SET_ID), "holdout-1-v2")

    def test_both_guards_compose_against_reuse(self) -> None:
        release = _release()
        changed = _manifest(calendar_version="cal-2")
        with self.assertRaises(DataDriftError):
            require_fingerprint_matches(_manifest(), dataset_fingerprint(changed))
        with self.assertRaises(ReaccessPolicyError):
            require_test_reaccess_compliance(
                release,
                _inputs(dataset_fingerprint="fp2", test_set_id=_TEST_SET_ID),
            )

    def test_holdout_unlocked_exactly_once_in_flow(self) -> None:
        registration = _registration()
        state = self._evaluated_state()
        guard_final_evaluation_read(registration, state.status)
        updated = mark_first_read(registration, state.status, read_at=_at(1))
        self.assertEqual(updated.first_read_at, _at(1))
        with self.assertRaises(HoldoutRegistrationError):
            mark_first_read(updated, state.status, read_at=_at(2))


if __name__ == "__main__":
    unittest.main()
