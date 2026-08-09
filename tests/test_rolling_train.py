"""Rolling training orchestration tests (MVP 3 / SP 3.33).

Verifies that every fold fits only on its training interval (每个折叠只在其
训练区间拟合) — the injected fit is rejected if it leaks past the fold's
training window and every registered trial is structurally bound to the fold's
train / validation bounds — and that parameters are selected per the
pre-registered rules (按预注册规则选择参数, SP 3.21).
"""

import json
import unittest
from dataclasses import replace
from datetime import date, timedelta

from harbor.core.backtest_domain import Market
from harbor.core.candidate_selection import (
    CandidateSelection,
    SelectionRules,
)
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.rolling_train import (
    RollingTrainError,
    RollingTrainRun,
    rolling_train_fingerprint,
    rolling_train_json,
    run_rolling_training,
)
from harbor.core.rolling_window import build_walk_forward_folds
from harbor.core.training_fit import build_training_fit
from harbor.core.trial_budget import BudgetExhaustedError, TieBreaker, TrialBudget
from harbor.core.validation_config import (
    MetricDirection,
    RetrainFrequency,
    RollingWindowConfig,
    RollingWindowMode,
    TuningConfig,
)
from harbor.core.validation_domain import (
    EvaluationSplit,
    Parameter,
    ParameterTrial,
)

_FINGERPRINT = "f" * 64


def _split(**overrides: object) -> EvaluationSplit:
    """Return a tight pre-registered split with overridable fields."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": date(2023, 1, 1),
        "test_end": date(2025, 12, 31),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _rolling(**overrides: object) -> RollingWindowConfig:
    """Return an expanding, every-fold rolling config with overridable fields."""
    fields: dict[str, object] = {
        "mode": RollingWindowMode.EXPANDING,
        "train_length_days": None,
        "step_days": 365,
        "retrain_frequency": RetrainFrequency.EVERY_FOLD,
    }
    fields.update(overrides)
    return RollingWindowConfig(**fields)  # type: ignore[arg-type]


def _sequence(**overrides: object):
    """Build the SP 3.31 fold sequence (defaults to 4 folds)."""
    fields: dict[str, object] = {
        "split": _split(),
        "rolling": _rolling(),
        "dataset_fingerprint": _FINGERPRINT,
    }
    fields.update(overrides)
    return build_walk_forward_folds(**fields)  # type: ignore[arg-type]


def _space() -> ParameterSpace:
    """Return a three-parameter space (two weights + one window)."""
    return build_parameter_space(
        declare_parameter(
            name="cash_weight",
            kind=ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.05,
            markets=(Market.HK, Market.US),
        ),
        declare_parameter(
            name="factor_weight",
            kind=ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.95,
            markets=(Market.HK, Market.US),
        ),
        declare_parameter(
            name="lookback",
            kind=ParameterKind.WINDOW,
            domain=ParameterDomain.INTEGER,
            minimum=60,
            maximum=504,
            step=24,
            default=252,
            markets=(Market.HK, Market.US),
        ),
    )


def _budget() -> TrialBudget:
    return TrialBudget(max_trials=3, random_seed=42)


def _tuning(**overrides: object) -> TuningConfig:
    fields: dict[str, object] = {
        "primary_metric": "sharpe",
        "metric_direction": MetricDirection.HIGHER_BETTER,
        "max_trials": 3,
        "random_seed": 42,
        "min_validation_days": 63,
    }
    fields.update(overrides)
    return TuningConfig(**fields)  # type: ignore[arg-type]


def _candidates(lookbacks: tuple[int, int, int] = (252, 252, 324)) -> list[dict[str, object]]:
    """Return three candidate parameter sets with the given lookbacks."""
    return [
        {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": lookbacks[0]},
        {"cash_weight": 0.10, "factor_weight": 0.90, "lookback": lookbacks[1]},
        {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": lookbacks[2]},
    ]


def _fit_factory(train_start: date, train_end: date):
    """Fit a snapshot confined to the requested training interval."""
    return build_training_fit(
        fit_start=train_start,
        fit_end=train_end,
        dataset_fingerprint=_FINGERPRINT,
        code_version="1.0.0",
        fitted_state=(("lookback", 252.0),),
    )


def _overshooting_fit_factory(train_start: date, train_end: date):
    """Fit a snapshot that leaks one day past the training interval."""
    return build_training_fit(
        fit_start=train_start,
        fit_end=train_end + timedelta(days=1),
        dataset_fingerprint=_FINGERPRINT,
        code_version="1.0.0",
        fitted_state=(("lookback", 252.0),),
    )


def _evaluate(fold, parameters: dict[str, object]) -> float:
    """Deterministic validation metric: larger lookback scores higher."""
    return int(parameters["lookback"]) / 1000.0


def _run(**overrides: object) -> RollingTrainRun:
    """Run the rolling training orchestration with overridable arguments."""
    fields: dict[str, object] = {
        "sequence": _sequence(),
        "space": _space(),
        "budget": _budget(),
        "market": Market.HK,
        "dataset_fingerprint": _FINGERPRINT,
        "code_version": "1.0.0",
        "tuning": _tuning(),
        "candidate_parameter_sets": _candidates(),
        "fit_factory": _fit_factory,
        "evaluate": _evaluate,
        "constraints": (),
        "validation_samples": 200,
    }
    fields.update(overrides)
    return run_rolling_training(**fields)  # type: ignore[arg-type]


def _trial(**overrides: object) -> ParameterTrial:
    """Return a valid parameter trial with overridable fields."""
    fields: dict[str, object] = {
        "trial_id": "trial-1",
        "parameters": (
            Parameter(name="cash_weight", value=0.05),
            Parameter(name="factor_weight", value=0.95),
            Parameter(name="lookback", value=252),
        ),
        "dataset_fingerprint": _FINGERPRINT,
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "seed": 42,
        "code_version": "1.0.0",
        "metric": 0.25,
    }
    fields.update(overrides)
    return ParameterTrial(**fields)  # type: ignore[arg-type]


class RollingTrainErrorTests(unittest.TestCase):
    """The dedicated error type and builder guards."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(RollingTrainError, ValueError))

    def test_empty_candidate_sets_rejected(self) -> None:
        with self.assertRaises(RollingTrainError):
            _run(candidate_parameter_sets=())

    def test_empty_dataset_fingerprint_rejected(self) -> None:
        with self.assertRaises(RollingTrainError):
            _run(dataset_fingerprint="")

    def test_non_positive_validation_samples_rejected(self) -> None:
        with self.assertRaises(RollingTrainError):
            _run(validation_samples=0)

    def test_dataset_fingerprint_mismatch_rejected(self) -> None:
        with self.assertRaises(RollingTrainError):
            _run(dataset_fingerprint="g" * 64)


class FoldBoundaryIsolationTests(unittest.TestCase):
    """Every fold fits only on its own training interval (SP 3.33 acceptance)."""

    def test_fit_confined_to_fold_training_window(self) -> None:
        run = _run()
        for result in run:
            self.assertEqual(result.fit.fit_start, result.fold.train_start)
            self.assertEqual(result.fit.fit_end, result.fold.train_end)

    def test_each_fold_uses_its_own_training_window(self) -> None:
        run = _run()
        self.assertGreater(run[1].fit.fit_end, run[0].fit.fit_end)
        self.assertEqual(run[0].fit.fit_end, date(2021, 12, 31))
        self.assertEqual(run[1].fit.fit_end, date(2022, 12, 31))

    def test_trials_bound_to_fold_train_and_validation(self) -> None:
        run = _run()
        for result in run:
            for trial in result.trials:
                self.assertEqual(trial.train_start, result.fold.train_start)
                self.assertEqual(trial.train_end, result.fold.train_end)
                self.assertEqual(trial.validation_start, result.fold.validation_start)
                self.assertEqual(trial.validation_end, result.fold.validation_end)

    def test_fit_leaking_past_training_rejected(self) -> None:
        with self.assertRaises(RollingTrainError) as ctx:
            _run(fit_factory=_overshooting_fit_factory)
        self.assertIn("not confined", str(ctx.exception))

    def test_fit_into_validation_rejected(self) -> None:
        def leaky(train_start: date, train_end: date):
            return build_training_fit(
                fit_start=train_start,
                fit_end=date(2022, 1, 1),  # validation_start of fold 0
                dataset_fingerprint=_FINGERPRINT,
                code_version="1.0.0",
                fitted_state=(("lookback", 252.0),),
            )

        with self.assertRaises(RollingTrainError):
            _run(fit_factory=leaky)


class PreRegisteredSelectionTests(unittest.TestCase):
    """Parameters are selected under the pre-registered rules (SP 3.21)."""

    def test_best_candidate_selected_per_fold(self) -> None:
        run = _run()
        for result in run:
            self.assertIsNotNone(result.selection.selected)
            assert result.selection.selected is not None
            self.assertEqual(result.selection.selected.parameter("lookback"), 324)

    def test_selected_trial_among_fold_trials(self) -> None:
        run = _run()
        for result in run:
            self.assertIn(
                result.selection.selected.trial_id,
                [trial.trial_id for trial in result.trials],
            )

    def test_selection_uses_pre_registered_rules(self) -> None:
        run = _run()
        for result in run:
            self.assertEqual(result.selection.rules.primary_metric, "sharpe")
            self.assertEqual(
                result.selection.rules.direction,
                MetricDirection.HIGHER_BETTER,
            )
            self.assertEqual(result.selection.rules.min_validation_samples, 63)

    def test_min_validation_samples_gate_excludes_all(self) -> None:
        run = _run(validation_samples=50)
        for result in run:
            self.assertIsNone(result.selection.selected)
            self.assertTrue(result.selection.excluded)
            self.assertIn(
                "below the pre-registered minimum",
                result.selection.excluded[0].reason,
            )

    def test_tie_breaker_first(self) -> None:
        run = _run(candidate_parameter_sets=_candidates(lookbacks=(324, 324, 252)))
        for result in run:
            self.assertEqual(
                result.selection.selected.trial_id,
                f"fold-trial-{result.fold.fold_index}-1",
            )

    def test_selection_is_deterministic(self) -> None:
        self.assertEqual(_run(), _run())


class OrchestrationTests(unittest.TestCase):
    """The run wires the per-fold pipeline into an auditable value."""

    def test_one_result_per_fold(self) -> None:
        run = _run()
        self.assertEqual(len(run), len(run.folds))
        self.assertEqual(len(run), 4)

    def test_folds_property_matches_sequence(self) -> None:
        sequence = _sequence()
        run = _run()
        self.assertEqual(run.folds, sequence.folds)

    def test_run_records_frozen_context(self) -> None:
        run = _run()
        self.assertEqual(run.market, Market.HK)
        self.assertEqual(run.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(run.code_version, "1.0.0")

    def test_trials_per_fold_and_unique_ids(self) -> None:
        run = _run()
        for result in run:
            self.assertEqual(len(result.trials), 3)
        all_ids = [trial.trial_id for result in run for trial in result.trials]
        self.assertEqual(len(set(all_ids)), len(all_ids))

    def test_budget_exhaustion_propagates(self) -> None:
        with self.assertRaises(BudgetExhaustedError):
            _run(candidate_parameter_sets=_candidates() + _candidates())

    def test_selection_for_lookup(self) -> None:
        run = _run()
        self.assertIsNotNone(run.selection_for(0))
        self.assertIsNotNone(run.selection_for(3))
        self.assertIsNone(run.selection_for(99))


class FoldTrainingResultValidationTests(unittest.TestCase):
    """The per-fold value rejects an inconsistent, leaking record."""

    def test_fit_dataset_fingerprint_mismatch_rejected(self) -> None:
        result = _run()[0]
        bad_fit = replace(result.fit, dataset_fingerprint="g" * 64, fingerprint="abc")
        with self.assertRaises(RollingTrainError):
            replace(result, fit=bad_fit)

    def test_trial_bounds_outside_fold_rejected(self) -> None:
        result = _run()[0]
        ghost = _trial(
            trial_id="ghost",
            train_start=date(2018, 1, 1),  # before the fold's training start
            train_end=date(2018, 12, 31),
        )
        with self.assertRaises(RollingTrainError):
            replace(result, trials=(ghost,))

    def test_fit_beyond_fold_training_rejected(self) -> None:
        result = _run()[0]
        bad_fit = replace(
            result.fit,
            fit_end=date(2022, 6, 30),  # inside the fold's validation window
            fingerprint="abc",
        )
        with self.assertRaises(RollingTrainError):
            replace(result, fit=bad_fit)

    def test_selected_not_among_trials_rejected(self) -> None:
        result = _run()[0]
        ghost = _trial(trial_id="ghost")
        selection = CandidateSelection(
            rules=SelectionRules(
                primary_metric="sharpe",
                direction=MetricDirection.HIGHER_BETTER,
                tie_breaker=TieBreaker.FIRST,
                min_validation_samples=1,
            ),
            selected=ghost,
            excluded=(),
            fingerprint="abc",
        )
        with self.assertRaises(RollingTrainError):
            replace(result, selection=selection)

    def test_readable(self) -> None:
        result = _run()[0]
        self.assertIn("fold 0", result.readable())
        self.assertIn("selected", result.readable())


class RollingTrainRunValidationTests(unittest.TestCase):
    """The run value rejects an inconsistent, un-auditable record."""

    def test_empty_results_rejected(self) -> None:
        run = _run()
        with self.assertRaises(RollingTrainError):
            replace(run, results=())

    def test_non_sequential_fold_indices_rejected(self) -> None:
        run = _run()
        second = replace(run[1], fold=replace(run[1].fold, fold_index=2))
        with self.assertRaises(RollingTrainError):
            replace(run, results=(run[0], second))

    def test_empty_fingerprint_rejected(self) -> None:
        run = _run()
        with self.assertRaises(RollingTrainError):
            replace(run, fingerprint="")

    def test_empty_dataset_fingerprint_rejected(self) -> None:
        run = _run()
        with self.assertRaises(RollingTrainError):
            replace(run, dataset_fingerprint="")

    def test_len_iter_getitem(self) -> None:
        run = _run()
        self.assertEqual(len(run), len(list(run)))
        self.assertEqual(list(run)[2].fold.fold_index, run[2].fold.fold_index)
        with self.assertRaises(IndexError):
            run[99]

    def test_readable(self) -> None:
        run = _run()
        self.assertIn("4 folds trained on HK", run.readable())
        self.assertIn("fp ", run.readable())


class FingerprintTests(unittest.TestCase):
    """The run fingerprint is stable and re-derivable."""

    def test_fingerprint_is_sha256_hex(self) -> None:
        self.assertRegex(_run().fingerprint, r"^[0-9a-f]{64}$")

    def test_fingerprint_rederivable(self) -> None:
        run = _run()
        self.assertEqual(run.fingerprint, rolling_train_fingerprint(run))

    def test_fingerprint_stable_across_equal_runs(self) -> None:
        self.assertEqual(_run().fingerprint, _run().fingerprint)

    def test_fingerprint_changes_with_candidate_metrics(self) -> None:
        self.assertNotEqual(
            _run(candidate_parameter_sets=_candidates(lookbacks=(252, 252, 324))).fingerprint,
            _run(candidate_parameter_sets=_candidates(lookbacks=(252, 252, 156))).fingerprint,
        )

    def test_fingerprint_changes_with_tuning(self) -> None:
        self.assertNotEqual(
            _run(tuning=_tuning(min_validation_days=10)).fingerprint,
            _run().fingerprint,
        )

    def test_fingerprint_changes_with_market(self) -> None:
        self.assertNotEqual(
            _run(market=Market.US).fingerprint,
            _run(market=Market.HK).fingerprint,
        )

    def test_fingerprint_changes_with_dataset_fingerprint(self) -> None:
        other_fp = "a" * 64

        def other_fit(train_start: date, train_end: date):
            return build_training_fit(
                fit_start=train_start,
                fit_end=train_end,
                dataset_fingerprint=other_fp,
                code_version="1.0.0",
                fitted_state=(("lookback", 252.0),),
            )

        run = _run(
            dataset_fingerprint=other_fp,
            sequence=_sequence(dataset_fingerprint=other_fp),
            fit_factory=other_fit,
        )
        self.assertNotEqual(run.fingerprint, _run().fingerprint)

    def test_fingerprint_changes_with_fit(self) -> None:
        self.assertNotEqual(
            _run(
                fit_factory=lambda train_start, train_end: build_training_fit(
                    fit_start=train_start,
                    fit_end=train_end,
                    dataset_fingerprint=_FINGERPRINT,
                    code_version="1.0.0",
                    fitted_state=(("lookback", 324.0),),
                )
            ).fingerprint,
            _run().fingerprint,
        )

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(rolling_train_json(_run()))
        self.assertNotIn("fingerprint", payload)
        self.assertIn("market", payload)
        self.assertIn("results", payload)
        self.assertEqual(len(payload["results"]), 4)

    def test_json_is_key_sorted(self) -> None:
        payload = json.loads(rolling_train_json(_run()))
        self.assertEqual(
            list(payload.keys()),
            ["code_version", "dataset_fingerprint", "market", "results"],
        )
        first = payload["results"][0]
        self.assertEqual(
            list(first.keys()),
            ["fit_fingerprint", "fold_index", "selected", "selection_fingerprint", "trials"],
        )
        self.assertEqual(first["fold_index"], 0)


if __name__ == "__main__":
    unittest.main()
