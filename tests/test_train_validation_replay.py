"""Training/validation replayability tests (MVP 3 / SP 3.28).

With a fixed data manifest, configuration and seed (固定数据清单、配置和种子),
the whole train/validation pipeline must replay identically (SP 3.28): the
parameter trials (SP 3.18), the ranking / selection (SP 3.21), and the fit
snapshots (SP 3.19) are byte-identical across two executions. These tests run
the full pipeline — fit (SP 3.19), registration (SP 3.18), validation
application (SP 3.20), selection (SP 3.21), multiple-trials penalty (SP 3.22)
and reconciliation (SP 3.23) — twice under the SAME fixed inputs and assert
every output and derived fingerprint is identical, plus that a changed seed or
configuration DOES change the outputs (so the determinism is not vacuous).
"""

import unittest
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.candidate_selection import (
    CandidateSelection,
    SelectionRules,
    TrialValidationResult,
    rules_from_tuning,
    select_candidate,
    selection_fingerprint,
)
from harbor.core.factor_standardization import (
    StandardizationConfig,
    StandardizationMethod,
)
from harbor.core.multiple_trial_penalty import (
    MultipleTrialPenalty,
    PenaltyConfig,
    compute_trial_penalty,
    penalty_fingerprint,
)
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.training_fit import (
    TrainingFit,
    build_training_fit,
    fit_fingerprint,
    standardization_fit,
)
from harbor.core.trial_budget import TrialBudget
from harbor.core.trial_reconciliation import (
    TrialReconciliation,
    TrialReconciliationSummary,
    reconcile_trials,
    reconciliation_fingerprint,
)
from harbor.core.trial_registry import (
    TrialRegistry,
    build_trial_registry,
    trial_fingerprint,
)
from harbor.core.validation_apply import (
    ValidationApplication,
    apply_fingerprint,
    apply_standardization,
    build_validation_application,
)
from harbor.core.validation_config import MetricDirection, TuningConfig
from harbor.core.validation_domain import EvaluationSplit, ParameterTrial

_DATASET_FINGERPRINT = "f" * 64
_CODE_VERSION = "1.0.0"
_SEED = 42

_TRAIN_START = date(2019, 1, 1)
_TRAIN_END = date(2020, 12, 31)
_VALIDATION_START = date(2021, 1, 1)
_VALIDATION_END = date(2022, 12, 31)
_VALIDATION_DATE = date(2021, 6, 1)

# The fixed deterministic trial sequence (parameters, validation metric).
_TRIALS: tuple[tuple[dict[str, object], float], ...] = (
    ({"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 252}, 0.12),
    ({"cash_weight": 0.10, "factor_weight": 0.90, "lookback": 300}, 0.15),
    ({"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 324}, 0.18),
)


@dataclass(frozen=True)
class ReplayResult:
    """Every output of one pipeline execution (SP 3.28)."""

    fit: TrainingFit
    fit_fingerprint: str
    applied: ValidationApplication
    applied_fingerprint: str
    trials: tuple[ParameterTrial, ...]
    trial_fingerprints: tuple[str, ...]
    selection: CandidateSelection
    selection_fingerprint: str
    penalty: MultipleTrialPenalty
    penalty_fingerprint: str
    reconciliation: TrialReconciliationSummary
    reconciliation_fingerprint: str


def _split(**overrides: object) -> EvaluationSplit:
    """Return a valid split with overridable boundaries."""
    fields: dict[str, object] = {
        "train_start": _TRAIN_START,
        "train_end": _TRAIN_END,
        "validation_start": _VALIDATION_START,
        "validation_end": _VALIDATION_END,
        "test_start": date(2023, 1, 2),
        "test_end": date(2024, 12, 31),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _space() -> object:
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


def _registry(*, seed: int = _SEED) -> TrialRegistry:
    """Return a trial registry over the fixed split context (SP 3.18)."""
    return build_trial_registry(
        space=_space(),
        budget=TrialBudget(max_trials=10, random_seed=42),
        dataset_fingerprint=_DATASET_FINGERPRINT,
        code_version=_CODE_VERSION,
        market=Market.HK,
        train_start=_TRAIN_START,
        train_end=_TRAIN_END,
        validation_start=_VALIDATION_START,
        validation_end=_VALIDATION_END,
        seed=seed,
        trial_prefix="trial",
    )


def _training_values() -> dict[date, dict[str, float | None]]:
    """The fixed training-period factor values (SP 3.19 input)."""
    return {
        date(2019, 1, 1): {"AAA": 0.10, "BBB": 0.05, "CCC": None},
        date(2020, 1, 1): {"AAA": 0.11, "BBB": 0.055, "CCC": None},
        date(2021, 1, 1): {"AAA": 0.12, "BBB": 0.06, "CCC": None},
    }


def _validation_values() -> dict[str, float | None]:
    """The fixed validation-day cross-section (SP 3.20 input)."""
    return {"AAA": 0.13, "BBB": 0.065, "CCC": None}


def _fit_snapshot(*, winsorize: float | None = 0.25) -> TrainingFit:
    """Fit the fixed training period (SP 3.19)."""
    config = StandardizationConfig(method=StandardizationMethod.ZSCORE, winsorize=winsorize)
    standardization = standardization_fit(_training_values(), config=config)
    return build_training_fit(
        fit_start=_TRAIN_START,
        fit_end=_TRAIN_END,
        dataset_fingerprint=_DATASET_FINGERPRINT,
        code_version=_CODE_VERSION,
        standardization=standardization,
    )


def _registered_trials(*, seed: int = _SEED) -> tuple[ParameterTrial, ...]:
    """Register the fixed trial sequence (SP 3.18)."""
    registry = _registry(seed=seed)
    trials: list[ParameterTrial] = []
    for parameters, metric in _TRIALS:
        registry, trial = registry.register(dict(parameters), metric=metric)
        trials.append(trial)
    return tuple(trials)


def _applied(fit: TrainingFit) -> ValidationApplication:
    """Apply the frozen fit on the validation day (SP 3.20)."""
    standardization = fit.standardization
    if standardization is None:
        raise AssertionError("fit must carry a standardization state.")
    applied_std = apply_standardization(
        _validation_values(),
        fit=standardization,
        decision_date=_VALIDATION_DATE,
    )
    return build_validation_application(
        fit=fit,
        decision_date=_VALIDATION_DATE,
        standardization=applied_std,
    )


def _rules() -> SelectionRules:
    """The pre-registered selection rules (SP 3.21)."""
    tuning = TuningConfig(
        primary_metric="sharpe",
        metric_direction=MetricDirection.HIGHER_BETTER,
        min_validation_days=63,
    )
    return rules_from_tuning(tuning)


def _selected(trials: tuple[ParameterTrial, ...]) -> CandidateSelection:
    """Select the best candidate under the pre-registered rules (SP 3.21)."""
    results = {
        trial.trial_id: TrialValidationResult(
            trial_id=trial.trial_id,
            metric_name="sharpe",
            validation_samples=200,
            risk={"max_drawdown_pct": 10.0},
        )
        for trial in trials
    }
    return select_candidate(trials, rules=_rules(), results=results)


def _penalty(trials: tuple[ParameterTrial, ...]) -> MultipleTrialPenalty:
    """Compute the multiple-trials penalty (SP 3.22)."""
    return compute_trial_penalty(
        trials,
        config=PenaltyConfig(),
        direction=MetricDirection.HIGHER_BETTER,
    )


def _reconciliation(trial: ParameterTrial) -> TrialReconciliation:
    """Return a closing reconciliation for a trial (SP 3.23)."""
    return TrialReconciliation(
        trial_id=trial.trial_id,
        ledger_reconciled=True,
        net_value_reconciled=True,
        attribution_reconciled=True,
    )


def _reconciled(trials: tuple[ParameterTrial, ...]) -> TrialReconciliationSummary:
    """Reconcile every trial (SP 3.23)."""
    reconciliations = {trial.trial_id: _reconciliation(trial) for trial in trials}
    return reconcile_trials(trials, reconciliations=reconciliations).summary


def _run(*, seed: int = _SEED, winsorize: float | None = 0.25) -> ReplayResult:
    """Run the full fixed-input pipeline once (SP 3.28)."""
    fit = _fit_snapshot(winsorize=winsorize)
    trials = _registered_trials(seed=seed)
    applied = _applied(fit)
    selection = _selected(trials)
    penalty = _penalty(trials)
    reconciliation = _reconciled(trials)
    return ReplayResult(
        fit=fit,
        fit_fingerprint=fit_fingerprint(fit),
        applied=applied,
        applied_fingerprint=apply_fingerprint(applied),
        trials=trials,
        trial_fingerprints=tuple(trial_fingerprint(trial) for trial in trials),
        selection=selection,
        selection_fingerprint=selection_fingerprint(selection),
        penalty=penalty,
        penalty_fingerprint=penalty_fingerprint(penalty),
        reconciliation=reconciliation,
        reconciliation_fingerprint=reconciliation_fingerprint(reconciliation),
    )


class ReplayDeterminismTests(unittest.TestCase):
    """Verifies two runs under the same fixed inputs replay identically."""

    def test_replay_identical_trials(self) -> None:
        run_a = _run()
        run_b = _run()
        self.assertEqual(run_a.trials, run_b.trials)
        self.assertEqual(run_a.trial_fingerprints, run_b.trial_fingerprints)

    def test_replay_identical_fit(self) -> None:
        run_a = _run()
        run_b = _run()
        self.assertEqual(run_a.fit, run_b.fit)
        self.assertEqual(run_a.fit_fingerprint, run_b.fit_fingerprint)

    def test_replay_identical_selection(self) -> None:
        run_a = _run()
        run_b = _run()
        self.assertEqual(run_a.selection, run_b.selection)
        self.assertEqual(run_a.selection_fingerprint, run_b.selection_fingerprint)

    def test_replay_identical_penalty(self) -> None:
        run_a = _run()
        run_b = _run()
        self.assertEqual(run_a.penalty, run_b.penalty)
        self.assertEqual(run_a.penalty_fingerprint, run_b.penalty_fingerprint)

    def test_replay_identical_reconciliation(self) -> None:
        run_a = _run()
        run_b = _run()
        self.assertEqual(run_a.reconciliation, run_b.reconciliation)
        self.assertEqual(run_a.reconciliation_fingerprint, run_b.reconciliation_fingerprint)

    def test_replay_identical_application(self) -> None:
        run_a = _run()
        run_b = _run()
        self.assertEqual(run_a.applied, run_b.applied)
        self.assertEqual(run_a.applied_fingerprint, run_b.applied_fingerprint)

    def test_full_replay_identical(self) -> None:
        # The acceptance: 参数试验、排名、选择和拟合快照完全一致.
        self.assertEqual(_run(), _run())


class FingerprintStabilityTests(unittest.TestCase):
    """Verifies each derived fingerprint is re-derivable (SP 3.28)."""

    def test_fit_fingerprint_rederivable(self) -> None:
        fit = _fit_snapshot()
        self.assertEqual(fit.fingerprint, fit_fingerprint(fit))

    def test_selection_fingerprint_rederivable(self) -> None:
        selection = _selected(_registered_trials())
        self.assertEqual(selection.fingerprint, selection_fingerprint(selection))

    def test_penalty_fingerprint_rederivable(self) -> None:
        penalty = _penalty(_registered_trials())
        self.assertEqual(penalty.fingerprint, penalty_fingerprint(penalty))

    def test_reconciliation_fingerprint_rederivable(self) -> None:
        reconciliation = _reconciled(_registered_trials())
        self.assertEqual(reconciliation.fingerprint, reconciliation_fingerprint(reconciliation))

    def test_application_fingerprint_rederivable(self) -> None:
        applied = _applied(_fit_snapshot())
        self.assertEqual(applied.fingerprint, apply_fingerprint(applied))


class FixedInputInvarianceTests(unittest.TestCase):
    """Verifies the determinism is anchored to the fixed inputs."""

    def test_different_seed_changes_trial_identity(self) -> None:
        run_a = _run(seed=1)
        run_b = _run(seed=2)
        self.assertNotEqual(run_a.trial_fingerprints, run_b.trial_fingerprints)

    def test_different_config_changes_fit(self) -> None:
        run_a = _run(winsorize=0.25)
        run_b = _run(winsorize=0.1)
        self.assertNotEqual(run_a.fit_fingerprint, run_b.fit_fingerprint)

    def test_same_fixed_inputs_stable_across_runs(self) -> None:
        self.assertEqual(_run(), _run())


if __name__ == "__main__":
    unittest.main()
