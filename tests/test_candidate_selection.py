"""Candidate parameter selection tests (MVP 3 / SP 3.21).

Verifies that parameter selection follows the pre-registered rules — ONE
primary metric, risk constraints, tie rules and a minimum validation-sample
count — and that it never uses test-set performance: the selection API only
reads the validation primary metric recorded on each trial, and a trial whose
recorded metric is not the pre-registered primary metric is excluded.
"""

import unittest
from datetime import date

from harbor.core.candidate_selection import (
    CandidateSelection,
    CandidateSelectionError,
    ExcludedCandidate,
    RiskConstraint,
    SelectionRules,
    TrialValidationResult,
    rules_from_tuning,
    select_candidate,
    selection_fingerprint,
    selection_json,
)
from harbor.core.trial_budget import TieBreaker
from harbor.core.validation_config import MetricDirection, TuningConfig
from harbor.core.validation_domain import Parameter, ParameterTrial

_TRAIN_START = date(2019, 1, 1)
_TRAIN_END = date(2020, 12, 31)
_VALIDATION_START = date(2021, 1, 1)
_VALIDATION_END = date(2022, 12, 31)


def _trial(**overrides: object) -> ParameterTrial:
    """Return a valid parameter trial with overridable fields."""
    fields: dict[str, object] = {
        "trial_id": "trial-1",
        "parameters": (Parameter(name="cash_weight", value=0.05),),
        "dataset_fingerprint": "fp-1",
        "train_start": _TRAIN_START,
        "train_end": _TRAIN_END,
        "validation_start": _VALIDATION_START,
        "validation_end": _VALIDATION_END,
        "seed": 42,
        "code_version": "1.0.0",
        "metric": 0.12,
    }
    fields.update(overrides)
    return ParameterTrial(**fields)  # type: ignore[arg-type]


def _constraint(**overrides: object) -> RiskConstraint:
    """Return a valid risk constraint with overridable fields."""
    fields: dict[str, object] = {
        "metric": "max_drawdown_pct",
        "maximum": 30.0,
        "description": "max drawdown",
    }
    fields.update(overrides)
    return RiskConstraint(**fields)  # type: ignore[arg-type]


def _rules(**overrides: object) -> SelectionRules:
    """Return pre-registered selection rules with overridable fields."""
    fields: dict[str, object] = {
        "primary_metric": "sharpe",
        "direction": MetricDirection.HIGHER_BETTER,
        "tie_breaker": TieBreaker.FIRST,
        "min_validation_samples": 10,
        "risk_constraints": (_constraint(),),
    }
    fields.update(overrides)
    return SelectionRules(**fields)  # type: ignore[arg-type]


def _result(**overrides: object) -> TrialValidationResult:
    """Return a valid trial validation result with overridable fields."""
    fields: dict[str, object] = {
        "trial_id": "trial-1",
        "metric_name": "sharpe",
        "validation_samples": 100,
        "risk": {"max_drawdown_pct": 12.0},
    }
    fields.update(overrides)
    return TrialValidationResult(**fields)  # type: ignore[arg-type]


def _results(*trials: tuple[str, ...]) -> dict[str, TrialValidationResult]:
    """Return validation results for the given trial ids."""
    return {trial_id: _result(trial_id=trial_id) for trial_id in trials}


class RiskConstraintTests(unittest.TestCase):
    """Validates the :class:`RiskConstraint` invariants."""

    def test_valid(self) -> None:
        constraint = _constraint()
        self.assertEqual(constraint.metric, "max_drawdown_pct")
        self.assertEqual(constraint.maximum, 30.0)

    def test_empty_metric_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            _constraint(metric="")

    def test_empty_description_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            _constraint(description="")

    def test_check_passes_at_boundary(self) -> None:
        self.assertTrue(_constraint().check(30.0))

    def test_check_passes_below(self) -> None:
        self.assertTrue(_constraint().check(12.0))

    def test_check_fails_above(self) -> None:
        self.assertFalse(_constraint().check(40.0))

    def test_check_fails_unmeasured(self) -> None:
        self.assertFalse(_constraint().check(None))

    def test_readable(self) -> None:
        self.assertIn("max_drawdown_pct <= 30.0", _constraint().readable())


class SelectionRulesTests(unittest.TestCase):
    """Validates the pre-registered :class:`SelectionRules`."""

    def test_defaults(self) -> None:
        rules = SelectionRules(primary_metric="sharpe")
        self.assertEqual(rules.direction, MetricDirection.HIGHER_BETTER)
        self.assertEqual(rules.tie_breaker, TieBreaker.FIRST)
        self.assertEqual(rules.min_validation_samples, 1)
        self.assertEqual(rules.risk_constraints, ())

    def test_valid(self) -> None:
        rules = _rules()
        self.assertEqual(rules.primary_metric, "sharpe")
        self.assertEqual(rules.min_validation_samples, 10)
        self.assertEqual(len(rules.risk_constraints), 1)

    def test_empty_primary_metric_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            _rules(primary_metric="   ")

    def test_non_positive_min_samples_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            _rules(min_validation_samples=0)

    def test_duplicate_risk_metrics_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            _rules(
                risk_constraints=(
                    _constraint(),
                    _constraint(metric="max_drawdown_pct", maximum=20.0),
                )
            )

    def test_readable(self) -> None:
        self.assertIn("selection by sharpe", _rules().readable())
        self.assertIn("min-samples 10", _rules().readable())


class TrialValidationResultTests(unittest.TestCase):
    """Validates the :class:`TrialValidationResult` invariants."""

    def test_valid(self) -> None:
        result = _result()
        self.assertEqual(result.trial_id, "trial-1")
        self.assertEqual(result.metric_name, "sharpe")
        self.assertEqual(result.validation_samples, 100)
        self.assertEqual(result.risk, {"max_drawdown_pct": 12.0})

    def test_empty_trial_id_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            _result(trial_id="")

    def test_empty_metric_name_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            _result(metric_name="")

    def test_non_positive_samples_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            _result(validation_samples=0)

    def test_readable(self) -> None:
        self.assertIn("trial trial-1 metric sharpe", _result().readable())


class ExcludedCandidateTests(unittest.TestCase):
    """Validates the :class:`ExcludedCandidate` invariants."""

    def test_valid(self) -> None:
        excluded = ExcludedCandidate("trial-1", "some reason")
        self.assertEqual(excluded.trial_id, "trial-1")
        self.assertEqual(excluded.reason, "some reason")

    def test_empty_trial_id_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            ExcludedCandidate("", "some reason")

    def test_empty_reason_rejected(self) -> None:
        with self.assertRaises(CandidateSelectionError):
            ExcludedCandidate("trial-1", "")

    def test_readable(self) -> None:
        self.assertEqual(
            ExcludedCandidate("trial-1", "some reason").readable(),
            "trial-1: some reason",
        )


class SelectCandidateTests(unittest.TestCase):
    """Verifies :func:`select_candidate` under the pre-registered rules."""

    def test_selects_highest_primary_metric(self) -> None:
        trials = [
            _trial(trial_id="trial-1", metric=0.10),
            _trial(trial_id="trial-2", metric=0.20),
        ]
        selection = select_candidate(trials, rules=_rules(), results=_results("trial-1", "trial-2"))
        self.assertIsNotNone(selection.selected)
        self.assertEqual(selection.selected.trial_id, "trial-2")
        self.assertEqual(selection.excluded, ())

    def test_selects_lowest_for_lower_better(self) -> None:
        trials = [
            _trial(trial_id="trial-1", metric=0.10),
            _trial(trial_id="trial-2", metric=0.20),
        ]
        selection = select_candidate(
            trials,
            rules=_rules(direction=MetricDirection.LOWER_BETTER),
            results=_results("trial-1", "trial-2"),
        )
        self.assertEqual(selection.selected.trial_id, "trial-1")

    def test_tie_first_keeps_earliest(self) -> None:
        trials = [
            _trial(trial_id="trial-1", metric=0.10),
            _trial(trial_id="trial-2", metric=0.10),
        ]
        selection = select_candidate(
            trials,
            rules=_rules(tie_breaker=TieBreaker.FIRST),
            results=_results("trial-1", "trial-2"),
        )
        self.assertEqual(selection.selected.trial_id, "trial-1")

    def test_tie_last_takes_latest(self) -> None:
        trials = [
            _trial(trial_id="trial-1", metric=0.10),
            _trial(trial_id="trial-2", metric=0.10),
        ]
        selection = select_candidate(
            trials,
            rules=_rules(tie_breaker=TieBreaker.LAST),
            results=_results("trial-1", "trial-2"),
        )
        self.assertEqual(selection.selected.trial_id, "trial-2")

    def test_tie_trial_id_lexicographic(self) -> None:
        trials = [
            _trial(trial_id="trial-9", metric=0.10),
            _trial(trial_id="trial-10", metric=0.10),
        ]
        selection = select_candidate(
            trials,
            rules=_rules(tie_breaker=TieBreaker.TRIAL_ID),
            results=_results("trial-9", "trial-10"),
        )
        self.assertEqual(selection.selected.trial_id, "trial-10")

    def test_failed_trial_excluded(self) -> None:
        trials = [
            _trial(trial_id="trial-1", metric=None, failed_reason="boom"),
            _trial(trial_id="trial-2", metric=0.20),
        ]
        selection = select_candidate(trials, rules=_rules(), results=_results("trial-2"))
        self.assertEqual(selection.selected.trial_id, "trial-2")
        self.assertEqual(len(selection.excluded), 1)
        self.assertIn("no primary metric", selection.excluded[0].reason)

    def test_trial_without_result_excluded(self) -> None:
        trials = [_trial(trial_id="trial-1", metric=0.20)]
        selection = select_candidate(trials, rules=_rules(), results={})
        self.assertIsNone(selection.selected)
        self.assertIn("no validation result", selection.excluded[0].reason)

    def test_metric_name_mismatch_excluded(self) -> None:
        # Selection must use ONLY the pre-registered primary metric: a trial
        # that recorded a different metric (e.g. sortino) is excluded.
        trials = [_trial(trial_id="trial-1", metric=0.20)]
        results = {"trial-1": _result(metric_name="sortino")}
        selection = select_candidate(trials, rules=_rules(), results=results)
        self.assertIsNone(selection.selected)
        self.assertIn("pre-registered primary metric is 'sharpe'", selection.excluded[0].reason)

    def test_below_min_samples_excluded(self) -> None:
        trials = [_trial(trial_id="trial-1", metric=0.20)]
        results = {"trial-1": _result(validation_samples=5)}
        selection = select_candidate(
            trials, rules=_rules(min_validation_samples=10), results=results
        )
        self.assertIsNone(selection.selected)
        self.assertIn("below the pre-registered minimum 10", selection.excluded[0].reason)

    def test_risk_constraint_violation_excluded(self) -> None:
        trials = [_trial(trial_id="trial-1", metric=0.20)]
        results = {"trial-1": _result(risk={"max_drawdown_pct": 40.0})}
        selection = select_candidate(trials, rules=_rules(), results=results)
        self.assertIsNone(selection.selected)
        self.assertIn("risk constraint", selection.excluded[0].reason)
        self.assertIn("violated", selection.excluded[0].reason)

    def test_unmeasured_risk_excluded(self) -> None:
        trials = [_trial(trial_id="trial-1", metric=0.20)]
        results = {"trial-1": _result(risk={"max_drawdown_pct": None})}
        selection = select_candidate(trials, rules=_rules(), results=results)
        self.assertIsNone(selection.selected)
        self.assertIn("unmeasured", selection.excluded[0].reason)

    def test_risk_passing_candidate_selected(self) -> None:
        trials = [_trial(trial_id="trial-1", metric=0.20)]
        results = {"trial-1": _result(risk={"max_drawdown_pct": 12.0})}
        selection = select_candidate(trials, rules=_rules(), results=results)
        self.assertEqual(selection.selected.trial_id, "trial-1")

    def test_no_eligible_candidate_selected_none(self) -> None:
        trials = [
            _trial(trial_id="trial-1", metric=None, failed_reason="boom"),
            _trial(trial_id="trial-2", metric=0.20),
        ]
        results = {"trial-2": _result(metric_name="sortino")}
        selection = select_candidate(trials, rules=_rules(), results=results)
        self.assertIsNone(selection.selected)
        self.assertEqual(len(selection.excluded), 2)

    def test_empty_trials(self) -> None:
        selection = select_candidate([], rules=_rules(), results={})
        self.assertIsNone(selection.selected)
        self.assertEqual(selection.excluded, ())

    def test_readable(self) -> None:
        selection = select_candidate(
            [_trial(trial_id="trial-1", metric=0.20)],
            rules=_rules(),
            results=_results("trial-1"),
        )
        self.assertIn("candidate selection by sharpe", selection.readable())
        self.assertIn("selected trial-1", selection.readable())


class RulesFromTuningTests(unittest.TestCase):
    """Verifies :func:`rules_from_tuning` maps the SP 3.2 config."""

    def test_maps_pre_registered_tuning_fields(self) -> None:
        tuning = TuningConfig(
            primary_metric="sortino",
            metric_direction=MetricDirection.LOWER_BETTER,
            min_validation_days=21,
        )
        rules = rules_from_tuning(tuning)
        self.assertEqual(rules.primary_metric, "sortino")
        self.assertEqual(rules.direction, MetricDirection.LOWER_BETTER)
        self.assertEqual(rules.min_validation_samples, 21)
        self.assertEqual(rules.tie_breaker, TieBreaker.FIRST)
        self.assertEqual(rules.risk_constraints, ())

    def test_custom_tie_breaker_and_risk(self) -> None:
        tuning = TuningConfig(primary_metric="sharpe")
        constraint = _constraint()
        rules = rules_from_tuning(
            tuning, tie_breaker=TieBreaker.LAST, risk_constraints=(constraint,)
        )
        self.assertEqual(rules.tie_breaker, TieBreaker.LAST)
        self.assertEqual(rules.risk_constraints, (constraint,))


class SelectionFingerprintTests(unittest.TestCase):
    """Verifies the selection fingerprint is stable and re-derivable."""

    def _selection(self) -> CandidateSelection:
        return select_candidate(
            [_trial(trial_id="trial-1", metric=0.20)],
            rules=_rules(),
            results=_results("trial-1"),
        )

    def test_fingerprint_stable_for_equal(self) -> None:
        self.assertEqual(
            selection_fingerprint(self._selection()),
            selection_fingerprint(self._selection()),
        )

    def test_fingerprint_changes_with_selected(self) -> None:
        changed = select_candidate(
            [_trial(trial_id="trial-2", metric=0.30)],
            rules=_rules(),
            results=_results("trial-2"),
        )
        self.assertNotEqual(
            selection_fingerprint(self._selection()),
            selection_fingerprint(changed),
        )

    def test_fingerprint_changes_with_rules(self) -> None:
        changed = select_candidate(
            [_trial(trial_id="trial-1", metric=0.20)],
            rules=_rules(min_validation_samples=50),
            results=_results("trial-1"),
        )
        self.assertNotEqual(
            selection_fingerprint(self._selection()),
            selection_fingerprint(changed),
        )

    def test_fingerprint_changes_with_exclusions(self) -> None:
        changed = select_candidate(
            [_trial(trial_id="trial-1", metric=0.20)],
            rules=_rules(),
            results={"trial-1": _result(risk={"max_drawdown_pct": 40.0})},
        )
        self.assertNotEqual(
            selection_fingerprint(self._selection()),
            selection_fingerprint(changed),
        )

    def test_build_records_rederivable_fingerprint(self) -> None:
        selection = self._selection()
        self.assertEqual(selection.fingerprint, selection_fingerprint(selection))
        self.assertEqual(len(selection.fingerprint), 64)

    def test_json_is_key_sorted_and_stable(self) -> None:
        self.assertEqual(selection_json(self._selection()), selection_json(self._selection()))
        self.assertIn('"primary_metric":"sharpe"', selection_json(self._selection()))


if __name__ == "__main__":
    unittest.main()
