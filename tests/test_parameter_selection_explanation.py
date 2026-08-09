"""Parameter selection explanation tests (MVP 3 / SP 3.29).

Verifies that a parameter selection is explained auditably: the chosen
parameters and their source basis (来源依据), why that combination was selected
under the pre-registered rules, and an explicit confirmation that the test set
was never used (未使用测试集) — enforced by the explanation builder and verified
against the SP 3.24 access-guard audit.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.candidate_selection import (
    CandidateSelection,
    SelectionRules,
    TrialValidationResult,
    select_candidate,
)
from harbor.core.holdout_registry import HoldoutPurpose, HoldoutRegistration
from harbor.core.parameter_selection_explanation import (
    ParameterSelectionExplanation,
    SelectionExplanationError,
    confirmation_statement,
    explain_selection,
    explanation_fingerprint,
    explanation_json,
    selection_basis,
    verify_test_set_unused,
)
from harbor.core.test_access_guard import AccessAuditEntry, AccessGuard, AccessKind
from harbor.core.trial_budget import TieBreaker
from harbor.core.validation_config import MetricDirection
from harbor.core.validation_domain import Parameter, ParameterTrial, ValidationStatus

_FINGERPRINT = "f" * 64


def _at(day: int = 1) -> datetime:
    """Return a fixed UTC-aware timestamp."""
    return datetime(2026, 1, day, tzinfo=timezone.utc)


def _trial(**overrides: object) -> ParameterTrial:
    """Return a valid parameter trial with overridable fields."""
    fields: dict[str, object] = {
        "trial_id": "trial-1",
        "parameters": (
            Parameter(name="cash_weight", value=0.05),
            Parameter(name="lookback", value=252),
        ),
        "dataset_fingerprint": _FINGERPRINT,
        "train_start": date(2019, 1, 1),
        "train_end": date(2020, 12, 31),
        "validation_start": date(2021, 1, 1),
        "validation_end": date(2022, 12, 31),
        "seed": 42,
        "code_version": "1.0.0",
        "metric": 0.12,
    }
    fields.update(overrides)
    return ParameterTrial(**fields)  # type: ignore[arg-type]


def _rules(**overrides: object) -> SelectionRules:
    """Return pre-registered selection rules with overridable fields."""
    fields: dict[str, object] = {
        "primary_metric": "sharpe",
        "direction": MetricDirection.HIGHER_BETTER,
        "tie_breaker": TieBreaker.FIRST,
        "min_validation_samples": 1,
        "risk_constraints": (),
    }
    fields.update(overrides)
    return SelectionRules(**fields)  # type: ignore[arg-type]


def _selection(*, metric: float = 0.18) -> CandidateSelection:
    """Return a real SP 3.21 selection where the best trial wins."""
    trials = [
        _trial(trial_id="trial-1", metric=0.12),
        _trial(trial_id="trial-2", metric=0.15),
        _trial(trial_id="trial-3", metric=metric),
    ]
    results = {
        trial.trial_id: TrialValidationResult(
            trial_id=trial.trial_id, metric_name="sharpe", validation_samples=200
        )
        for trial in trials
    }
    return select_candidate(trials, rules=_rules(), results=results)


def _excluded_selection() -> CandidateSelection:
    """Return a selection where every candidate is excluded."""
    trials = [_trial(trial_id="trial-1", metric=None, failed_reason="boom")]
    return select_candidate(trials, rules=_rules(), results={})


def _explanation(**overrides: object) -> ParameterSelectionExplanation:
    """Return a valid explanation with overridable fields."""
    fields: dict[str, object] = {
        "selected": _trial(trial_id="trial-3", metric=0.18),
        "rules": _rules(),
        "basis": "selected [cash_weight=0.05, lookback=252] because it was best.",
        "test_set_used": False,
        "test_set_confirmation": confirmation_statement(),
        "source_selection_fingerprint": "fp-1",
        "dataset_fingerprint": _FINGERPRINT,
        "code_version": "1.0.0",
        "fingerprint": "exp-1",
    }
    fields.update(overrides)
    return ParameterSelectionExplanation(**fields)  # type: ignore[arg-type]


def _registration(**overrides: object) -> HoldoutRegistration:
    """Return a registered holdout with overridable fields."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "purpose": HoldoutPurpose.FINAL_EVALUATION,
        "created_at": _at(1),
        "authorized_stage": ValidationStatus.TEST_LOCKED,
    }
    fields.update(overrides)
    return HoldoutRegistration(**fields)  # type: ignore[arg-type]


def _guard(**overrides: object) -> AccessGuard:
    """Return an access guard with overridable fields."""
    fields: dict[str, object] = {"registration": _registration(), "audit": ()}
    fields.update(overrides)
    return AccessGuard(**fields)  # type: ignore[arg-type]


class SelectionBasisTests(unittest.TestCase):
    """Verifies :func:`selection_basis` renders the source rationale."""

    def test_basis_names_parameters_and_metric(self) -> None:
        basis = selection_basis(_selection())
        self.assertIn("cash_weight=0.05", basis)
        self.assertIn("lookback=252", basis)
        self.assertIn("sharpe (0.18)", basis)

    def test_basis_names_rules(self) -> None:
        basis = selection_basis(_selection())
        self.assertIn("direction higher_better", basis)
        self.assertIn("tie-break first", basis)
        self.assertIn("min-samples 1", basis)

    def test_basis_explains_no_candidate(self) -> None:
        basis = selection_basis(_excluded_selection())
        self.assertIn("no candidate selected", basis)
        self.assertIn("every trial was excluded", basis)


class ExplainSelectionTests(unittest.TestCase):
    """Verifies :func:`explain_selection` builds the persistable explanation."""

    def test_records_selected_parameters(self) -> None:
        explanation = explain_selection(
            _selection(), dataset_fingerprint=_FINGERPRINT, code_version="1.0.0"
        )
        self.assertIsNotNone(explanation.selected)
        self.assertEqual(explanation.selected.trial_id, "trial-3")
        self.assertEqual(explanation.selected.parameters[0].name, "cash_weight")

    def test_records_source_selection_fingerprint(self) -> None:
        selection = _selection()
        explanation = explain_selection(
            selection, dataset_fingerprint=_FINGERPRINT, code_version="1.0.0"
        )
        self.assertEqual(explanation.source_selection_fingerprint, selection.fingerprint)

    def test_test_set_used_is_false(self) -> None:
        explanation = explain_selection(
            _selection(), dataset_fingerprint=_FINGERPRINT, code_version="1.0.0"
        )
        self.assertFalse(explanation.test_set_used)
        self.assertIn("never read", explanation.test_set_confirmation)

    def test_test_set_used_true_rejected(self) -> None:
        with self.assertRaises(SelectionExplanationError):
            explain_selection(
                _selection(),
                dataset_fingerprint=_FINGERPRINT,
                code_version="1.0.0",
                test_set_used=True,
            )

    def test_fingerprint_rederivable(self) -> None:
        explanation = explain_selection(
            _selection(), dataset_fingerprint=_FINGERPRINT, code_version="1.0.0"
        )
        self.assertEqual(explanation.fingerprint, explanation_fingerprint(explanation))
        self.assertEqual(len(explanation.fingerprint), 64)

    def test_readable(self) -> None:
        explanation = explain_selection(
            _selection(), dataset_fingerprint=_FINGERPRINT, code_version="1.0.0"
        )
        self.assertIn("selection explanation", explanation.readable())
        self.assertIn("trial-3", explanation.readable())


class ExplanationValidationTests(unittest.TestCase):
    """Validates the :class:`ParameterSelectionExplanation` invariants."""

    def test_empty_basis_rejected(self) -> None:
        with self.assertRaises(SelectionExplanationError):
            _explanation(basis="")

    def test_test_set_used_rejected(self) -> None:
        with self.assertRaises(SelectionExplanationError):
            _explanation(test_set_used=True)

    def test_empty_confirmation_rejected(self) -> None:
        with self.assertRaises(SelectionExplanationError):
            _explanation(test_set_confirmation="")

    def test_empty_source_fingerprint_rejected(self) -> None:
        with self.assertRaises(SelectionExplanationError):
            _explanation(source_selection_fingerprint="")

    def test_empty_dataset_fingerprint_rejected(self) -> None:
        with self.assertRaises(SelectionExplanationError):
            _explanation(dataset_fingerprint="")

    def test_empty_code_version_rejected(self) -> None:
        with self.assertRaises(SelectionExplanationError):
            _explanation(code_version="")

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(SelectionExplanationError):
            _explanation(fingerprint="")


class TestSetUnusedVerificationTests(unittest.TestCase):
    """Verifies :func:`verify_test_set_unused` confirms 未使用测试集."""

    def test_verify_returns_confirmation_on_clean_audit(self) -> None:
        confirmation = verify_test_set_unused(_guard())
        self.assertEqual(confirmation, confirmation_statement())

    def test_verify_rejects_granted_parameter_comparison(self) -> None:
        entry = AccessAuditEntry(
            access_kind=AccessKind.PARAMETER_COMPARISON,
            test_set_id="holdout-1",
            stage=ValidationStatus.TEST_LOCKED,
            granted=True,
            reason=None,
            requested_at=_at(2),
        )
        guard = _guard(audit=(entry,))
        with self.assertRaises(SelectionExplanationError):
            verify_test_set_unused(guard)

    def test_verify_ignores_denied_comparison(self) -> None:
        entry = AccessAuditEntry(
            access_kind=AccessKind.PARAMETER_COMPARISON,
            test_set_id="holdout-1",
            stage=ValidationStatus.TUNING,
            granted=False,
            reason="selection cannot use the test set",
            requested_at=_at(2),
        )
        guard = _guard(audit=(entry,))
        self.assertEqual(verify_test_set_unused(guard), confirmation_statement())


class ExplanationFingerprintTests(unittest.TestCase):
    """Verifies the explanation fingerprint is stable and sensitive."""

    def _explanation(self) -> ParameterSelectionExplanation:
        return explain_selection(
            _selection(), dataset_fingerprint=_FINGERPRINT, code_version="1.0.0"
        )

    def test_fingerprint_stable_for_equal(self) -> None:
        self.assertEqual(
            explanation_fingerprint(self._explanation()),
            explanation_fingerprint(self._explanation()),
        )

    def test_fingerprint_changes_with_selected(self) -> None:
        changed = explain_selection(
            _selection(metric=0.25),
            dataset_fingerprint=_FINGERPRINT,
            code_version="1.0.0",
        )
        self.assertNotEqual(
            explanation_fingerprint(self._explanation()),
            explanation_fingerprint(changed),
        )

    def test_fingerprint_changes_with_rules(self) -> None:
        selection = _selection()
        changed = _explanation(
            selected=selection.selected,
            rules=_rules(min_validation_samples=10),
            source_selection_fingerprint=selection.fingerprint,
        )
        self.assertNotEqual(
            explanation_fingerprint(self._explanation()),
            explanation_fingerprint(changed),
        )

    def test_json_is_key_sorted_and_stable(self) -> None:
        self.assertEqual(
            explanation_json(self._explanation()),
            explanation_json(self._explanation()),
        )
        self.assertIn('"primary_metric":"sharpe"', explanation_json(self._explanation()))


if __name__ == "__main__":
    unittest.main()
