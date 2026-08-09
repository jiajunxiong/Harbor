"""Future-function regression tests (MVP 3 / SP 3.25).

The out-of-sample discipline forbids look-ahead (未来函数): a training fit
and the registered trials must never depend on market data, financials or
corporate actions that occur after the training/validation boundary. These
tests change 行情 (market data / prices), 财报 (financials) and 企业行动
(corporate actions) after the validation period and confirm:

- the training-period fit (SP 3.19) and its snapshot fingerprint are
  UNCHANGED — because fitting pools only training-period data, and a fit that
  would pool later data is rejected by ``require_fit_within_training``;
- the registered trials (SP 3.18) are UNCHANGED — their inputs and
  fingerprints are frozen at registration and never depend on future-period
  outcomes;
- the validation-period application (SP 3.20) uses only the frozen training
  fit and is rejected for test-period dates;
- the test-set access guard (SP 3.24) denies reading test-period data before
  ``TEST_LOCKED``.

The dataset is built so that only the future slice differs between the base
and the modified version, proving the invariance is due to training-only
fitting rather than an unchanged dataset.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Market
from harbor.core.factor_standardization import (
    StandardizationConfig,
    StandardizationMethod,
)
from harbor.core.holdout_registry import HoldoutPurpose, HoldoutRegistration
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.test_access_guard import AccessGuard, AccessGuardError, AccessKind
from harbor.core.training_fit import (
    TrainingFit,
    TrainingFitError,
    build_training_fit,
    fit_fingerprint,
    industry_baseline_fit,
    require_fit_within_training,
    standardization_fit,
)
from harbor.core.trial_budget import TrialBudget
from harbor.core.trial_registry import (
    TrialRegistry,
    build_trial_registry,
    trial_fingerprint,
)
from harbor.core.validation_apply import (
    ValidationApplyError,
    apply_standardization,
    require_application_in_validation,
)
from harbor.core.validation_domain import EvaluationSplit, ValidationStatus

_TRAIN_START = date(2019, 1, 1)
_TRAIN_END = date(2021, 12, 31)
_VALIDATION_START = date(2022, 1, 3)
_VALIDATION_END = date(2022, 12, 30)
_TEST_START = date(2023, 1, 2)
_TEST_END = date(2024, 12, 31)

_TRAIN_DATES = (date(2019, 1, 1), date(2020, 1, 1), date(2021, 1, 1))
_VALIDATION_DATES = (date(2022, 1, 3), date(2022, 6, 1))
_TEST_DATES = (date(2023, 1, 2), date(2024, 1, 2))

# The three future-sensitive data kinds named in the acceptance.
_FACTORS = ("momentum", "earnings_yield", "dividend_yield")


def _base_factors() -> dict[str, dict[date, dict[str, float | None]]]:
    """Return per-factor raw values across train/validation/test dates.

    ``momentum`` derives from 行情 (prices), ``earnings_yield`` from 财报
    (financials) and ``dividend_yield`` from 企业行动 (corporate actions).
    """
    all_dates = _TRAIN_DATES + _VALIDATION_DATES + _TEST_DATES
    momentum: dict[date, dict[str, float | None]] = {}
    earnings: dict[date, dict[str, float | None]] = {}
    dividend: dict[date, dict[str, float | None]] = {}
    for index, day in enumerate(all_dates):
        momentum[day] = {
            "AAA": 0.10 + 0.01 * index,
            "BBB": 0.05 + 0.005 * index,
            "CCC": None,
        }
        earnings[day] = {"AAA": 0.06, "BBB": 0.04, "CCC": 0.02}
        dividend[day] = {"AAA": 0.03, "BBB": 0.02, "CCC": 0.01}
    return {
        "momentum": momentum,
        "earnings_yield": earnings,
        "dividend_yield": dividend,
    }


def _future_modified_factors() -> dict[str, dict[date, dict[str, float | None]]]:
    """Return the base dataset with 行情/财报/企业行动 changed AFTER training.

    The training dates are left untouched; a late validation date and every
    test date are drastically modified so the change is real.
    """
    base = _base_factors()
    modified: dict[str, dict[date, dict[str, float | None]]] = {
        factor: dict(values) for factor, values in base.items()
    }
    for factor in modified:
        for day in _TEST_DATES:
            modified[factor][day] = {symbol: 9.99 for symbol in modified[factor][day]}
        modified[factor][_VALIDATION_DATES[1]] = {
            symbol: (8.88 if value is not None else None)
            for symbol, value in modified[factor][_VALIDATION_DATES[1]].items()
        }
    return modified


def _slice(
    factor_values: dict[date, dict[str, float | None]],
    dates: tuple[date, ...],
) -> dict[date, dict[str, float | None]]:
    """Return the factor values restricted to ``dates``."""
    return {day: factor_values[day] for day in dates}


def _split(**overrides: object) -> EvaluationSplit:
    """Return a valid split with overridable boundaries."""
    fields: dict[str, object] = {
        "train_start": _TRAIN_START,
        "train_end": _TRAIN_END,
        "validation_start": _VALIDATION_START,
        "validation_end": _VALIDATION_END,
        "test_start": _TEST_START,
        "test_end": _TEST_END,
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _config(**overrides: object) -> StandardizationConfig:
    """Return a standardization config with overridable fields."""
    fields: dict[str, object] = {
        "method": StandardizationMethod.ZSCORE,
        "winsorize": 0.25,
    }
    fields.update(overrides)
    return StandardizationConfig(**fields)  # type: ignore[arg-type]


def _training_fit(
    factor: str, factors: dict[str, dict[date, dict[str, float | None]]]
) -> TrainingFit:
    """Fit the training period for ``factor`` and assemble a snapshot (SP 3.19)."""
    standardization = standardization_fit(_slice(factors[factor], _TRAIN_DATES), config=_config())
    return build_training_fit(
        fit_start=_TRAIN_START,
        fit_end=_TRAIN_END,
        dataset_fingerprint="f" * 64,
        code_version="1.0.0",
        standardization=standardization,
    )


def _space() -> object:
    """Return a small bounded parameter space (SP 3.15)."""
    return build_parameter_space(
        declare_parameter(
            name="cash_weight",
            kind=ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.05,
            markets=(Market.HK,),
        )
    )


def _registry(**overrides: object) -> TrialRegistry:
    """Return a trial registry over the frozen split context (SP 3.18)."""
    kwargs: dict[str, object] = {
        "space": _space(),
        "budget": TrialBudget(max_trials=10, random_seed=42),
        "dataset_fingerprint": "f" * 64,
        "code_version": "1.0.0",
        "market": Market.HK,
        "train_start": _TRAIN_START,
        "train_end": _TRAIN_END,
        "validation_start": _VALIDATION_START,
        "validation_end": _VALIDATION_END,
        "seed": 42,
        "trial_prefix": "trial",
    }
    kwargs.update(overrides)
    return build_trial_registry(**kwargs)  # type: ignore[arg-type]


def _registration(**overrides: object) -> HoldoutRegistration:
    """Return a registered holdout (SP 3.5) with overridable fields."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "purpose": HoldoutPurpose.FINAL_EVALUATION,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "authorized_stage": ValidationStatus.TEST_LOCKED,
        "split": _split(),
        "config_hash": "abc123",
    }
    fields.update(overrides)
    return HoldoutRegistration(**fields)  # type: ignore[arg-type]


class FutureModificationTests(unittest.TestCase):
    """Verifies the dataset modification is real and future-only."""

    def test_training_slices_identical_for_all_data_kinds(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        for factor in _FACTORS:
            self.assertEqual(
                _slice(base[factor], _TRAIN_DATES),
                _slice(modified[factor], _TRAIN_DATES),
            )

    def test_test_period_differs_for_all_data_kinds(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        for factor in _FACTORS:
            self.assertNotEqual(
                _slice(base[factor], _TEST_DATES),
                _slice(modified[factor], _TEST_DATES),
            )

    def test_late_validation_differs(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        self.assertNotEqual(
            _slice(base["momentum"], _VALIDATION_DATES[1:]),
            _slice(modified["momentum"], _VALIDATION_DATES[1:]),
        )

    def test_base_is_stable_across_calls(self) -> None:
        self.assertEqual(_base_factors(), _base_factors())


class TrainingFitInvarianceTests(unittest.TestCase):
    """Verifies the training fit is unchanged by future data (训练结果不变)."""

    def test_momentum_fit_invariant_to_future_prices(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        fit_base = standardization_fit(_slice(base["momentum"], _TRAIN_DATES), config=_config())
        fit_modified = standardization_fit(
            _slice(modified["momentum"], _TRAIN_DATES), config=_config()
        )
        self.assertEqual(fit_base, fit_modified)

    def test_earnings_fit_invariant_to_future_financials(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        fit_base = standardization_fit(
            _slice(base["earnings_yield"], _TRAIN_DATES), config=_config()
        )
        fit_modified = standardization_fit(
            _slice(modified["earnings_yield"], _TRAIN_DATES), config=_config()
        )
        self.assertEqual(fit_base, fit_modified)

    def test_dividend_fit_invariant_to_future_corporate_actions(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        fit_base = standardization_fit(
            _slice(base["dividend_yield"], _TRAIN_DATES), config=_config()
        )
        fit_modified = standardization_fit(
            _slice(modified["dividend_yield"], _TRAIN_DATES), config=_config()
        )
        self.assertEqual(fit_base, fit_modified)

    def test_training_snapshot_identical_after_future_change(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        self.assertEqual(
            _training_fit("momentum", base),
            _training_fit("momentum", modified),
        )

    def test_fit_fingerprint_unchanged_after_future_change(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        self.assertEqual(
            fit_fingerprint(_training_fit("momentum", base)),
            fit_fingerprint(_training_fit("momentum", modified)),
        )

    def test_industry_baseline_invariant_to_future_data(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        base_input = {
            "tech": _slice(base["momentum"], _TRAIN_DATES)[_TRAIN_DATES[0]],
            "bank": _slice(base["earnings_yield"], _TRAIN_DATES)[_TRAIN_DATES[0]],
        }
        modified_input = {
            "tech": _slice(modified["momentum"], _TRAIN_DATES)[_TRAIN_DATES[0]],
            "bank": _slice(modified["earnings_yield"], _TRAIN_DATES)[_TRAIN_DATES[0]],
        }
        self.assertEqual(base_input, modified_input)
        self.assertEqual(
            industry_baseline_fit(base_input),
            industry_baseline_fit(modified_input),
        )

    def test_lookahead_fit_changes_with_future_data(self) -> None:
        # A fit that (incorrectly) pools validation AND test data DOES change
        # when future data changes — proving the invariance above comes from
        # fitting on the training period only, not from an unchanged dataset.
        base = _base_factors()
        modified = _future_modified_factors()
        lookahead = _TRAIN_DATES + _VALIDATION_DATES + _TEST_DATES
        fit_base = standardization_fit(_slice(base["momentum"], lookahead), config=_config())
        fit_modified = standardization_fit(
            _slice(modified["momentum"], lookahead), config=_config()
        )
        self.assertNotEqual(fit_base, fit_modified)

    def test_fit_spanning_into_validation_rejected(self) -> None:
        # A snapshot claiming it was fit into the validation period is
        # rejected by the SP 3.19 guard — a look-ahead fit cannot be recorded.
        standardization = standardization_fit(
            _slice(_base_factors()["momentum"], _TRAIN_DATES), config=_config()
        )
        snapshot = build_training_fit(
            fit_start=_TRAIN_START,
            fit_end=_VALIDATION_END,
            dataset_fingerprint="f" * 64,
            code_version="1.0.0",
            standardization=standardization,
        )
        with self.assertRaises(TrainingFitError):
            require_fit_within_training(snapshot, _split())


class RegisteredTrialInvarianceTests(unittest.TestCase):
    """Verifies the registered trials are unchanged by future data (已登记试验不变)."""

    def test_trial_fingerprint_stable_across_registrations(self) -> None:
        registry, trial = _registry().register({"cash_weight": 0.05}, metric=0.12)
        registry2, trial2 = _registry().register({"cash_weight": 0.05}, metric=0.12)
        self.assertEqual(trial_fingerprint(trial), trial_fingerprint(trial2))
        self.assertEqual(trial, trial2)

    def test_trial_inputs_unchanged_even_if_future_changes_metric(self) -> None:
        # The recorded trial identity covers the INPUTS only: a future-data
        # change would alter a recomputed validation metric, but the recorded
        # trial (and its fingerprint) is unchanged because it is frozen at
        # registration.
        _, trial_before = _registry().register({"cash_weight": 0.05}, metric=0.12)
        _, trial_after = _registry().register({"cash_weight": 0.05}, metric=0.15)
        self.assertEqual(trial_fingerprint(trial_before), trial_fingerprint(trial_after))
        self.assertEqual(trial_before.parameters, trial_after.parameters)
        self.assertEqual(trial_before.dataset_fingerprint, trial_after.dataset_fingerprint)
        self.assertEqual(trial_before.seed, trial_after.seed)
        self.assertEqual(trial_before.code_version, trial_after.code_version)
        self.assertEqual(trial_before.train_start, trial_after.train_start)
        self.assertEqual(trial_before.validation_end, trial_after.validation_end)

    def test_registered_trial_is_immutable(self) -> None:
        registry, trial = _registry().register({"cash_weight": 0.05}, metric=0.12)
        self.assertEqual(registry.trials, (trial,))
        # Registering again returns a NEW registry; the original is unchanged.
        registry2, _ = registry.register({"cash_weight": 0.05}, metric=0.12)
        self.assertEqual(registry.trials, (trial,))
        self.assertEqual(len(registry2.trials), 2)

    def test_trial_records_frozen_validation_boundaries(self) -> None:
        _, trial = _registry().register({"cash_weight": 0.05}, metric=0.12)
        self.assertEqual(trial.validation_start, _VALIDATION_START)
        self.assertEqual(trial.validation_end, _VALIDATION_END)

    def test_trial_fingerprint_excludes_metric(self) -> None:
        _, trial_a = _registry().register({"cash_weight": 0.05}, metric=0.10)
        _, trial_b = _registry().register({"cash_weight": 0.05}, metric=0.50)
        self.assertEqual(trial_fingerprint(trial_a), trial_fingerprint(trial_b))


class ApplicationWindowGuardTests(unittest.TestCase):
    """Verifies validation-period application uses only the frozen fit (SP 3.20)."""

    def test_test_period_application_rejected(self) -> None:
        snapshot = _training_fit("momentum", _base_factors())
        with self.assertRaises(ValidationApplyError):
            require_application_in_validation(_TEST_DATES[0], snapshot, _split())

    def test_validation_application_allowed(self) -> None:
        snapshot = _training_fit("momentum", _base_factors())
        require_application_in_validation(_VALIDATION_DATES[0], snapshot, _split())

    def test_application_uses_frozen_fit_unchanged_by_future_data(self) -> None:
        base = _base_factors()
        modified = _future_modified_factors()
        snapshot = _training_fit("momentum", base)
        snapshot_after = _training_fit("momentum", modified)
        values = {"AAA": 0.09, "BBB": 0.03}
        applied_before = apply_standardization(
            values, fit=snapshot.standardization, decision_date=_VALIDATION_DATES[0]
        )
        applied_after = apply_standardization(
            values,
            fit=snapshot_after.standardization,
            decision_date=_VALIDATION_DATES[0],
        )
        self.assertEqual(applied_before, applied_after)

    def test_fit_extending_into_validation_rejected_on_application(self) -> None:
        standardization = standardization_fit(
            _slice(_base_factors()["momentum"], _TRAIN_DATES), config=_config()
        )
        snapshot = build_training_fit(
            fit_start=_TRAIN_START,
            fit_end=_VALIDATION_END,
            dataset_fingerprint="f" * 64,
            code_version="1.0.0",
            standardization=standardization,
        )
        with self.assertRaises(TrainingFitError):
            require_application_in_validation(_VALIDATION_DATES[0], snapshot, _split())


class FutureFunctionGuardTests(unittest.TestCase):
    """Verifies the SP 3.24 guard denies future-data access before TEST_LOCKED."""

    def test_test_data_read_denied_before_test_locked(self) -> None:
        guard = AccessGuard(registration=_registration())
        with self.assertRaises(AccessGuardError):
            guard.require(AccessKind.DATA_READ, current_stage=ValidationStatus.TUNING)

    def test_test_metric_computation_denied_before_test_locked(self) -> None:
        guard = AccessGuard(registration=_registration())
        with self.assertRaises(AccessGuardError):
            guard.require(
                AccessKind.METRIC_COMPUTATION,
                current_stage=ValidationStatus.DATA_FROZEN,
            )


if __name__ == "__main__":
    unittest.main()
