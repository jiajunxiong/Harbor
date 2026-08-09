"""Multiple-trials penalty tests (MVP 3 / SP 3.22).

Verifies that the selection-bias penalty reports the trial count, the
effective degrees of freedom (distinct SP 3.18 input fingerprints) and a
selection-bias warning, and lowers the conclusion grade when the trial budget
is large or the best-vs-runner-up metric gap is below the significance
threshold.
"""

import unittest
from datetime import date

from harbor.core.multiple_trial_penalty import (
    MultipleTrialPenalty,
    MultipleTrialPenaltyError,
    PenaltyConfig,
    compute_trial_penalty,
    penalty_fingerprint,
    penalty_json,
)
from harbor.core.validation_config import MetricDirection
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


def _independent_trials(*metrics: float, start_seed: int = 1) -> list[ParameterTrial]:
    """Return independent trials (distinct seeds) with the given metrics."""
    return [
        _trial(trial_id=f"trial-{index}", metric=metric, seed=seed)
        for index, (metric, seed) in enumerate(
            zip(metrics, range(start_seed, start_seed + len(metrics)))
        )
    ]


def _config(**overrides: object) -> PenaltyConfig:
    """Return a penalty config with overridable thresholds."""
    fields: dict[str, object] = {
        "large_budget_threshold": 20,
        "min_significant_gap": 0.01,
    }
    fields.update(overrides)
    return PenaltyConfig(**fields)  # type: ignore[arg-type]


def _penalty(**overrides: object) -> MultipleTrialPenalty:
    """Return a valid penalty record with overridable fields."""
    fields: dict[str, object] = {
        "trial_count": 3,
        "effective_df": 2,
        "duplicates": 1,
        "downgrade": 1,
        "selection_bias_warning": "selection bias",
        "reasons": ("large trial budget: 3 trials searched",),
        "best_metric": 0.30,
        "runner_up_metric": 0.10,
        "metric_gap": 0.20,
        "fingerprint": "fp",
    }
    fields.update(overrides)
    return MultipleTrialPenalty(**fields)  # type: ignore[arg-type]


class PenaltyConfigTests(unittest.TestCase):
    """Validates the :class:`PenaltyConfig` invariants."""

    def test_defaults(self) -> None:
        config = PenaltyConfig()
        self.assertEqual(config.large_budget_threshold, 20)
        self.assertEqual(config.min_significant_gap, 0.01)

    def test_threshold_must_exceed_one(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _config(large_budget_threshold=1)

    def test_negative_gap_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _config(min_significant_gap=-0.01)

    def test_readable(self) -> None:
        self.assertIn("large-budget-threshold 20", _config().readable())


class MultipleTrialPenaltyTests(unittest.TestCase):
    """Validates the :class:`MultipleTrialPenalty` invariants."""

    def test_valid(self) -> None:
        penalty = _penalty()
        self.assertEqual(penalty.trial_count, 3)
        self.assertEqual(penalty.effective_df, 2)
        self.assertEqual(penalty.duplicates, 1)
        self.assertEqual(penalty.downgrade, 1)

    def test_negative_trial_count_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _penalty(trial_count=-1, effective_df=0, duplicates=0, downgrade=0, reasons=())

    def test_effective_df_above_trial_count_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _penalty(effective_df=4)

    def test_duplicates_mismatch_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _penalty(duplicates=2)

    def test_downgrade_out_of_range_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _penalty(
                downgrade=3,
                reasons=("a", "b", "c"),
            )

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _penalty(fingerprint="")

    def test_reasons_without_downgrade_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _penalty(downgrade=0, reasons=("unexpected",))

    def test_downgrade_without_reasons_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _penalty(downgrade=1, reasons=())

    def test_gap_without_metrics_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _penalty(best_metric=None, runner_up_metric=None, metric_gap=0.20)

    def test_metrics_without_gap_rejected(self) -> None:
        with self.assertRaises(MultipleTrialPenaltyError):
            _penalty(metric_gap=None)

    def test_readable(self) -> None:
        self.assertIn("trials 3 effective_df 2", _penalty().readable())


class ComputeTrialPenaltyTests(unittest.TestCase):
    """Verifies :func:`compute_trial_penalty` over the registered trials."""

    def test_reports_trial_count_and_effective_df(self) -> None:
        penalty = compute_trial_penalty(_independent_trials(0.10, 0.20), config=_config())
        self.assertEqual(penalty.trial_count, 2)
        self.assertEqual(penalty.effective_df, 2)
        self.assertEqual(penalty.duplicates, 0)

    def test_duplicate_inputs_collapse_effective_df(self) -> None:
        # Two trials share identical inputs (same seed 42), so they do not add
        # independent freedom: effective df 2, one duplicate.
        trials = [
            _trial(trial_id="trial-1", metric=0.10, seed=42),
            _trial(trial_id="trial-2", metric=0.20, seed=42),
            _trial(trial_id="trial-3", metric=0.30, seed=43),
        ]
        penalty = compute_trial_penalty(trials, config=_config())
        self.assertEqual(penalty.trial_count, 3)
        self.assertEqual(penalty.effective_df, 2)
        self.assertEqual(penalty.duplicates, 1)

    def test_single_trial_no_warning_no_downgrade(self) -> None:
        penalty = compute_trial_penalty(_independent_trials(0.10), config=_config())
        self.assertIsNone(penalty.selection_bias_warning)
        self.assertEqual(penalty.downgrade, 0)
        self.assertEqual(penalty.reasons, ())
        self.assertEqual(penalty.best_metric, 0.10)

    def test_no_trials_warning_no_downgrade(self) -> None:
        penalty = compute_trial_penalty([], config=_config())
        self.assertEqual(penalty.trial_count, 0)
        self.assertEqual(penalty.effective_df, 0)
        self.assertEqual(penalty.downgrade, 0)
        self.assertEqual(penalty.selection_bias_warning, "no trials recorded")
        self.assertIsNone(penalty.best_metric)

    def test_multiple_trials_selection_bias_warning(self) -> None:
        penalty = compute_trial_penalty(_independent_trials(0.10, 0.20), config=_config())
        self.assertIsNotNone(penalty.selection_bias_warning)
        self.assertIn("selection bias", penalty.selection_bias_warning)
        self.assertIn("2 trials", penalty.selection_bias_warning)
        self.assertIn("effective df 2", penalty.selection_bias_warning)

    def test_large_budget_downgrades_one_level(self) -> None:
        trials = [
            _trial(trial_id=f"trial-{i}", metric=0.30 if i == 1 else 0.10, seed=i)
            for i in range(1, 21)
        ]
        penalty = compute_trial_penalty(trials, config=_config())
        self.assertEqual(penalty.trial_count, 20)
        self.assertEqual(penalty.downgrade, 1)
        self.assertIn("large trial budget: 20 trials searched", penalty.reasons)
        # The best-vs-runner-up gap (0.20) is significant, so only the large
        # budget contributes the downgrade.
        self.assertNotIn("significance", penalty.reasons[0])
        self.assertIn("under a large trial budget", penalty.selection_bias_warning)

    def test_insignificant_gap_downgrades_one_level(self) -> None:
        trials = _independent_trials(0.100, 0.105)
        penalty = compute_trial_penalty(trials, config=_config(large_budget_threshold=100))
        self.assertEqual(penalty.downgrade, 1)
        self.assertIn("below the significance threshold", penalty.reasons[0])

    def test_large_budget_and_insignificant_gap_downgrade_two(self) -> None:
        trials = [
            _trial(trial_id=f"trial-{i}", metric=0.105 if i == 1 else 0.100, seed=i)
            for i in range(1, 21)
        ]
        penalty = compute_trial_penalty(trials, config=_config())
        self.assertEqual(penalty.downgrade, 2)
        self.assertEqual(len(penalty.reasons), 2)

    def test_significant_gap_small_budget_no_downgrade(self) -> None:
        trials = _independent_trials(0.10, 0.30)
        penalty = compute_trial_penalty(trials, config=_config())
        self.assertEqual(penalty.downgrade, 0)
        self.assertEqual(penalty.reasons, ())
        self.assertAlmostEqual(penalty.best_metric, 0.30)
        self.assertAlmostEqual(penalty.runner_up_metric, 0.10)
        self.assertAlmostEqual(penalty.metric_gap, 0.20)

    def test_lower_better_direction_gap_is_absolute(self) -> None:
        trials = _independent_trials(0.05, 0.20)
        penalty = compute_trial_penalty(
            trials, config=_config(), direction=MetricDirection.LOWER_BETTER
        )
        self.assertAlmostEqual(penalty.best_metric, 0.05)
        self.assertAlmostEqual(penalty.runner_up_metric, 0.20)
        self.assertAlmostEqual(penalty.metric_gap, 0.15)
        self.assertEqual(penalty.downgrade, 0)

    def test_failed_trials_count_but_never_score(self) -> None:
        trials = [
            _trial(trial_id="trial-1", metric=None, failed_reason="boom", seed=1),
            _trial(trial_id="trial-2", metric=0.20, seed=2),
        ]
        penalty = compute_trial_penalty(trials, config=_config())
        self.assertEqual(penalty.trial_count, 2)
        self.assertAlmostEqual(penalty.best_metric, 0.20)
        self.assertIsNone(penalty.runner_up_metric)
        self.assertIsNone(penalty.metric_gap)

    def test_readable(self) -> None:
        penalty = compute_trial_penalty(_independent_trials(0.10, 0.20), config=_config())
        self.assertIn("penalty trials 2 effective_df 2", penalty.readable())


class PenaltyFingerprintTests(unittest.TestCase):
    """Verifies the penalty fingerprint is stable and re-derivable."""

    def _penalty(self) -> MultipleTrialPenalty:
        return compute_trial_penalty(_independent_trials(0.10, 0.20), config=_config())

    def test_fingerprint_stable_for_equal(self) -> None:
        self.assertEqual(
            penalty_fingerprint(self._penalty()),
            penalty_fingerprint(self._penalty()),
        )

    def test_fingerprint_changes_with_metrics(self) -> None:
        changed = compute_trial_penalty(_independent_trials(0.10, 0.30), config=_config())
        self.assertNotEqual(
            penalty_fingerprint(self._penalty()),
            penalty_fingerprint(changed),
        )

    def test_fingerprint_changes_with_config(self) -> None:
        changed = compute_trial_penalty(
            _independent_trials(0.10, 0.20),
            config=_config(min_significant_gap=0.5),
        )
        self.assertNotEqual(
            penalty_fingerprint(self._penalty()),
            penalty_fingerprint(changed),
        )

    def test_compute_records_rederivable_fingerprint(self) -> None:
        penalty = self._penalty()
        self.assertEqual(penalty.fingerprint, penalty_fingerprint(penalty))
        self.assertEqual(len(penalty.fingerprint), 64)

    def test_json_is_key_sorted_and_stable(self) -> None:
        self.assertEqual(penalty_json(self._penalty()), penalty_json(self._penalty()))
        self.assertIn('"trial_count":2', penalty_json(self._penalty()))


if __name__ == "__main__":
    unittest.main()
