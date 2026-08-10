"""Test-set re-access policy tests (MVP 3 / SP 3.42).

Verifies that after the final test completes any substantive adjustment to the
strategy, parameters, data or code requires creating a new test-set version
and a new validation run (最终测试完成后任何策略、参数、数据或代码实质性调整均要
求创建新的测试集版本和新的验证运行), and that unchanged inputs may reuse the
finalized test set.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.final_holdout import (
    FinalHoldoutInputs,
    FinalHoldoutRelease,
    unlock_final_holdout,
)
from harbor.core.holdout_registry import register_test_set
from harbor.core.reaccess_policy import (
    ReaccessInputChange,
    ReaccessPolicyDecision,
    ReaccessPolicyError,
    bump_test_set_version,
    check_test_reaccess,
    compare_reaccess_inputs,
    require_test_reaccess_compliance,
)
from harbor.core.validation_domain import EvaluationSplit, ValidationStatus

_FINGERPRINT = "f" * 64
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CREATED = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _split(**overrides: object) -> EvaluationSplit:
    """Return a tight pre-registered split (final fold is full-length)."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": date(2023, 1, 1),
        "test_end": date(2026, 12, 30),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _registration(**overrides: object):
    """Register the independent holdout over the base split."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "split": _split(),
        "config_hash": "cfg-hash",
        "created_at": _CREATED,
    }
    fields.update(overrides)
    return register_test_set(**fields)  # type: ignore[arg-type]


def _inputs(**overrides: object) -> FinalHoldoutInputs:
    """Return the frozen final-evaluation inputs with overridable fields."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "dataset_fingerprint": _FINGERPRINT,
        "config_hash": "cfg-hash",
        "selection_fingerprint": "s" * 64,
        "code_version": "1.0.0",
    }
    fields.update(overrides)
    return FinalHoldoutInputs(**fields)  # type: ignore[arg-type]


def _release(**overrides: object) -> FinalHoldoutRelease:
    """Unlock the final holdout (SP 3.41) with overridable arguments."""
    fields: dict[str, object] = {
        "registration": _registration(),
        "current_stage": ValidationStatus.TEST_LOCKED,
        "responsibility": "Research Lead",
        "inputs": _inputs(),
        "unlocked_at": _AT,
    }
    fields.update(overrides)
    return unlock_final_holdout(**fields)  # type: ignore[arg-type]


def _decision(**overrides: object) -> ReaccessPolicyDecision:
    """A minimal policy decision for value-type tests."""
    fields: dict[str, object] = {
        "changes": (),
        "reuses_finalized_test_set": True,
        "requires_new_test_set": False,
        "requires_new_validation_run": False,
        "reason": None,
    }
    fields.update(overrides)
    return ReaccessPolicyDecision(**fields)  # type: ignore[arg-type]


class ReaccessPolicyErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(ReaccessPolicyError, ValueError))

    def test_bump_empty_id_rejected(self) -> None:
        with self.assertRaises(ReaccessPolicyError):
            bump_test_set_version("")


class CompareInputsTests(unittest.TestCase):
    """The four-way substantive-change comparison (策略/参数/数据/代码)."""

    def test_unchanged_inputs(self) -> None:
        self.assertEqual(compare_reaccess_inputs(_release(), _inputs()), ())

    def test_data_change_detected(self) -> None:
        changes = compare_reaccess_inputs(_release(), _inputs(dataset_fingerprint="d" * 64))
        self.assertEqual(changes, (ReaccessInputChange.DATA,))

    def test_strategy_change_detected(self) -> None:
        changes = compare_reaccess_inputs(_release(), _inputs(config_hash="other"))
        self.assertEqual(changes, (ReaccessInputChange.STRATEGY,))

    def test_parameters_change_detected(self) -> None:
        changes = compare_reaccess_inputs(_release(), _inputs(selection_fingerprint="t" * 64))
        self.assertEqual(changes, (ReaccessInputChange.PARAMETERS,))

    def test_code_change_detected(self) -> None:
        changes = compare_reaccess_inputs(_release(), _inputs(code_version="2.0.0"))
        self.assertEqual(changes, (ReaccessInputChange.CODE,))

    def test_all_changes_in_canonical_order(self) -> None:
        changes = compare_reaccess_inputs(
            _release(),
            _inputs(
                dataset_fingerprint="d" * 64,
                config_hash="other",
                selection_fingerprint="t" * 64,
                code_version="2.0.0",
            ),
        )
        self.assertEqual(
            changes,
            (
                ReaccessInputChange.DATA,
                ReaccessInputChange.STRATEGY,
                ReaccessInputChange.PARAMETERS,
                ReaccessInputChange.CODE,
            ),
        )

    def test_subset_preserves_canonical_order(self) -> None:
        changes = compare_reaccess_inputs(
            _release(),
            _inputs(config_hash="other", code_version="2.0.0"),
        )
        self.assertEqual(
            changes,
            (ReaccessInputChange.STRATEGY, ReaccessInputChange.CODE),
        )


class ReaccessPolicyDecisionTests(unittest.TestCase):
    """The policy decision value type invariants."""

    def test_no_change_decision(self) -> None:
        decision = _decision()
        self.assertFalse(decision.requires_new_test_set)
        self.assertFalse(decision.requires_new_validation_run)
        self.assertIsNone(decision.reason)

    def test_changed_reuse_decision(self) -> None:
        decision = _decision(
            changes=(ReaccessInputChange.DATA,),
            reuses_finalized_test_set=True,
            requires_new_test_set=True,
            requires_new_validation_run=True,
            reason="changed: data; new test set and validation run required",
        )
        self.assertTrue(decision.requires_new_test_set)
        self.assertTrue(decision.requires_new_validation_run)

    def test_changed_new_test_set_decision(self) -> None:
        decision = _decision(
            changes=(ReaccessInputChange.CODE,),
            reuses_finalized_test_set=False,
            requires_new_test_set=False,
            requires_new_validation_run=True,
            reason="changed: code; new validation run required",
        )
        self.assertFalse(decision.requires_new_test_set)
        self.assertTrue(decision.requires_new_validation_run)

    def test_new_validation_run_requires_change(self) -> None:
        with self.assertRaises(ReaccessPolicyError):
            _decision(requires_new_validation_run=True)

    def test_new_test_set_requires_change_and_reuse(self) -> None:
        with self.assertRaises(ReaccessPolicyError):
            _decision(
                changes=(ReaccessInputChange.DATA,),
                reuses_finalized_test_set=False,
                requires_new_test_set=True,
                requires_new_validation_run=True,
                reason="changed: data",
            )

    def test_changed_decision_requires_reason(self) -> None:
        with self.assertRaises(ReaccessPolicyError):
            _decision(
                changes=(ReaccessInputChange.DATA,),
                requires_new_test_set=True,
                requires_new_validation_run=True,
                reason=None,
            )

    def test_readable(self) -> None:
        self.assertIn("no substantive change", _decision().readable())
        changed = _decision(
            changes=(ReaccessInputChange.DATA,),
            reuses_finalized_test_set=True,
            requires_new_test_set=True,
            requires_new_validation_run=True,
            reason="changed: data",
        )
        self.assertIn("new test set required", changed.readable())


class CheckTestReaccessTests(unittest.TestCase):
    """The non-raising policy check."""

    def test_unchanged_inputs_may_reuse(self) -> None:
        decision = check_test_reaccess(_release(), _inputs())
        self.assertTrue(decision.reuses_finalized_test_set)
        self.assertFalse(decision.requires_new_test_set)
        self.assertFalse(decision.requires_new_validation_run)

    def test_data_change_mandates_new_test_set_and_run(self) -> None:
        decision = check_test_reaccess(_release(), _inputs(dataset_fingerprint="d" * 64))
        self.assertTrue(decision.reuses_finalized_test_set)
        self.assertTrue(decision.requires_new_test_set)
        self.assertTrue(decision.requires_new_validation_run)
        self.assertIsNotNone(decision.reason)
        assert decision.reason is not None
        self.assertIn("data", decision.reason)

    def test_changed_inputs_with_new_test_set(self) -> None:
        decision = check_test_reaccess(
            _release(),
            _inputs(test_set_id="holdout-1-v2", code_version="2.0.0"),
        )
        self.assertFalse(decision.reuses_finalized_test_set)
        self.assertFalse(decision.requires_new_test_set)
        self.assertTrue(decision.requires_new_validation_run)

    def test_multiple_changes_named_in_reason(self) -> None:
        decision = check_test_reaccess(
            _release(),
            _inputs(config_hash="other", code_version="2.0.0"),
        )
        self.assertIsNotNone(decision.reason)
        assert decision.reason is not None
        self.assertIn("strategy", decision.reason)
        self.assertIn("code", decision.reason)

    def test_readable(self) -> None:
        decision = check_test_reaccess(_release(), _inputs())
        self.assertIn("reuse", decision.readable())


class RequireComplianceTests(unittest.TestCase):
    """The raising enforcement of the re-access policy."""

    def test_unchanged_returns_decision(self) -> None:
        decision = require_test_reaccess_compliance(_release(), _inputs())
        self.assertFalse(decision.requires_new_test_set)

    def test_changed_reuse_raises(self) -> None:
        with self.assertRaises(ReaccessPolicyError):
            require_test_reaccess_compliance(_release(), _inputs(dataset_fingerprint="d" * 64))

    def test_changed_new_test_set_does_not_raise(self) -> None:
        decision = require_test_reaccess_compliance(
            _release(),
            _inputs(test_set_id="holdout-1-v2", code_version="2.0.0"),
        )
        self.assertTrue(decision.requires_new_validation_run)
        self.assertFalse(decision.requires_new_test_set)

    def test_error_mandates_new_test_set_and_run(self) -> None:
        with self.assertRaises(ReaccessPolicyError) as ctx:
            require_test_reaccess_compliance(_release(), _inputs(selection_fingerprint="t" * 64))
        message = str(ctx.exception)
        self.assertIn("parameters", message)
        self.assertIn("new test-set version", message)
        self.assertIn("new validation run", message)


class BumpTestSetVersionTests(unittest.TestCase):
    """Deriving the next test-set version id."""

    def test_base_id_becomes_v2(self) -> None:
        self.assertEqual(bump_test_set_version("holdout-1"), "holdout-1-v2")

    def test_v2_becomes_v3(self) -> None:
        self.assertEqual(bump_test_set_version("holdout-1-v2"), "holdout-1-v3")

    def test_trailing_suffix_only(self) -> None:
        self.assertEqual(bump_test_set_version("holdout-1-v2-x3"), "holdout-1-v2-x3-v2")

    def test_non_numeric_suffix_treated_as_base(self) -> None:
        self.assertEqual(bump_test_set_version("holdout-new"), "holdout-new-v2")

    def test_empty_id_rejected(self) -> None:
        with self.assertRaises(ReaccessPolicyError):
            bump_test_set_version("")
