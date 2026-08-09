"""Trial registration and fingerprint tests (MVP 3 / SP 3.18).

Verifies that every trial records its parameters, train/validation
boundaries, dataset fingerprint, seed, code version and metric/failure
reason, that the trial fingerprint is stable for equal inputs and excludes
execution identity and outputs, and that the registry enforces the parameter
space, the SP 3.16 constraints and the SP 3.17 budget (never silently
appending beyond the cap).
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.parameter_constraints import ConstraintKind, constraint
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.trial_budget import BudgetExhaustedError, TrialBudget
from harbor.core.trial_registry import (
    TrialRegistrationError,
    TrialRegistry,
    build_trial_registry,
    trial_fingerprint,
)
from harbor.core.validation_domain import ParameterTrial

_FINGERPRINT = "f" * 64
_TRAIN_START = date(2019, 1, 1)
_TRAIN_END = date(2020, 12, 31)
_VALIDATION_START = date(2021, 1, 1)
_VALIDATION_END = date(2022, 12, 31)


def _space() -> object:
    """Return a small bounded parameter space (continuous + integer)."""
    return build_parameter_space(
        declare_parameter(
            "weight_a",
            ParameterKind.FACTOR_WEIGHT,
            minimum=0.0,
            maximum=1.0,
            step=0.1,
            default=0.4,
        ),
        declare_parameter(
            "weight_b",
            ParameterKind.FACTOR_WEIGHT,
            minimum=0.0,
            maximum=1.0,
            step=0.1,
            default=0.6,
        ),
        declare_parameter(
            "position_count",
            ParameterKind.POSITION_COUNT,
            domain=ParameterDomain.INTEGER,
            minimum=5,
            maximum=20,
            default=10,
        ),
    )


def _budget(max_trials: int = 3) -> TrialBudget:
    """Return a deterministic trial budget."""
    return TrialBudget(max_trials=max_trials, random_seed=42)


def _registry(max_trials: int = 3, **overrides: object) -> TrialRegistry:
    """Return a trial registry over a frozen dataset context."""
    kwargs: dict[str, object] = {
        "space": _space(),  # type: ignore[arg-type]
        "budget": _budget(max_trials),
        "dataset_fingerprint": _FINGERPRINT,
        "code_version": "1.0.0",
        "market": Market.HK,
        "train_start": _TRAIN_START,
        "train_end": _TRAIN_END,
        "validation_start": _VALIDATION_START,
        "validation_end": _VALIDATION_END,
        "seed": 42,
    }
    kwargs.update(overrides)
    return build_trial_registry(**kwargs)  # type: ignore[arg-type]


def _trial(
    trial_id: str,
    parameters: tuple = (),
    dataset_fingerprint: str = _FINGERPRINT,
    metric: float | None = 0.5,
    seed: int = 42,
    code_version: str = "1.0.0",
) -> ParameterTrial:
    """Return a minimal parameter trial for fingerprint testing."""
    return ParameterTrial(
        trial_id=trial_id,
        parameters=parameters,
        dataset_fingerprint=dataset_fingerprint,
        train_start=_TRAIN_START,
        train_end=_TRAIN_END,
        validation_start=_VALIDATION_START,
        validation_end=_VALIDATION_END,
        seed=seed,
        code_version=code_version,
        metric=metric,
        failed_reason=None if metric is not None else "failed",
    )


class TrialFingerprintTests(unittest.TestCase):
    """Verify the trial fingerprint (试验指纹)."""

    def test_is_sha256_hexdigest(self) -> None:
        self.assertEqual(len(trial_fingerprint(_trial("t1"))), 64)

    def test_stable_across_equal_trials(self) -> None:
        first = _trial("t1")
        second = _trial("t2")
        self.assertEqual(trial_fingerprint(first), trial_fingerprint(second))

    def test_excludes_trial_id(self) -> None:
        self.assertEqual(
            trial_fingerprint(_trial("t1")),
            trial_fingerprint(_trial("t-other")),
        )

    def test_excludes_metric_and_failure(self) -> None:
        self.assertEqual(
            trial_fingerprint(_trial("t1", metric=0.5)),
            trial_fingerprint(_trial("t1", metric=0.9)),
        )
        self.assertEqual(
            trial_fingerprint(_trial("t1", metric=0.5)),
            trial_fingerprint(_trial("t1", metric=None)),
        )

    def test_changes_with_parameters(self) -> None:
        from harbor.core.validation_domain import Parameter

        base = _trial("t1")
        different = _trial(
            "t2",
            parameters=(Parameter(name="weight_a", value=0.2),),
        )
        self.assertNotEqual(trial_fingerprint(base), trial_fingerprint(different))

    def test_changes_with_dataset_fingerprint(self) -> None:
        base = _trial("t1")
        different = _trial("t2", dataset_fingerprint="0" * 64)
        self.assertNotEqual(trial_fingerprint(base), trial_fingerprint(different))

    def test_changes_with_boundaries(self) -> None:
        base = _trial("t1")
        different = ParameterTrial(
            trial_id="t2",
            parameters=(),
            dataset_fingerprint=_FINGERPRINT,
            train_start=date(2018, 1, 1),
            train_end=date(2019, 12, 31),
            validation_start=date(2020, 1, 1),
            validation_end=date(2021, 12, 31),
            seed=42,
            code_version="1.0.0",
            metric=0.5,
        )
        self.assertNotEqual(trial_fingerprint(base), trial_fingerprint(different))

    def test_changes_with_seed(self) -> None:
        base = _trial("t1", seed=42)
        different = _trial("t2", seed=7)
        self.assertNotEqual(trial_fingerprint(base), trial_fingerprint(different))

    def test_changes_with_code_version(self) -> None:
        base = _trial("t1", code_version="1.0.0")
        different = _trial("t2", code_version="2.0.0")
        self.assertNotEqual(trial_fingerprint(base), trial_fingerprint(different))


class TrialRegistryTests(unittest.TestCase):
    """Verify trial registration records the full audit context."""

    def test_register_records_all_fields(self) -> None:
        registry, trial = _registry().register(
            {"weight_a": 0.4, "weight_b": 0.6, "position_count": 12},
            metric=0.61,
        )
        self.assertEqual(trial.trial_id, "trial-1")
        self.assertEqual(trial.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(trial.train_start, _TRAIN_START)
        self.assertEqual(trial.validation_end, _VALIDATION_END)
        self.assertEqual(trial.seed, 42)
        self.assertEqual(trial.code_version, "1.0.0")
        self.assertEqual(trial.metric, 0.61)
        self.assertIsNone(trial.failed_reason)
        self.assertEqual(registry.used, 1)

    def test_register_returns_new_registry_and_accumulates(self) -> None:
        registry = _registry()
        registry, _ = registry.register({"weight_a": 0.4}, metric=0.5)
        registry, second = registry.register({"weight_a": 0.5}, metric=0.6)
        self.assertEqual(second.trial_id, "trial-2")
        self.assertEqual(registry.used, 2)
        self.assertEqual([t.trial_id for t in registry.trials], ["trial-1", "trial-2"])

    def test_register_is_immutable(self) -> None:
        registry = _registry()
        registry.register({"weight_a": 0.4}, metric=0.5)
        self.assertEqual(registry.used, 0)

    def test_register_records_failure_reason(self) -> None:
        _, trial = _registry().register(
            {"weight_a": 0.4},
            metric=None,
            failed_reason="validation ledger did not close",
        )
        self.assertIsNone(trial.metric)
        self.assertEqual(trial.failed_reason, "validation ledger did not close")

    def test_remaining_and_exhausted(self) -> None:
        registry = _registry(max_trials=2)
        self.assertEqual(registry.remaining, 2)
        self.assertFalse(registry.exhausted)
        registry, _ = registry.register({"weight_a": 0.4}, metric=0.5)
        self.assertEqual(registry.remaining, 1)
        registry, _ = registry.register({"weight_a": 0.5}, metric=0.6)
        self.assertTrue(registry.exhausted)
        self.assertEqual(registry.remaining, 0)

    def test_budget_exhausted_raises(self) -> None:
        registry = _registry(max_trials=1)
        registry, _ = registry.register({"weight_a": 0.4}, metric=0.5)
        with self.assertRaises(BudgetExhaustedError):
            registry.register({"weight_a": 0.5}, metric=0.6)

    def test_registered_trial_fingerprint_matches(self) -> None:
        _, trial = _registry().register(
            {"weight_a": 0.4, "position_count": 12},
            metric=0.61,
        )
        self.assertEqual(trial_fingerprint(trial), trial_fingerprint(trial))

    def test_register_rejects_undeclared_parameter(self) -> None:
        from harbor.core.parameter_space import UndeclaredParameterError

        with self.assertRaises(UndeclaredParameterError):
            _registry().register({"weight_momentum": 0.3}, metric=0.5)

    def test_register_rejects_constraint_violation(self) -> None:
        from harbor.core.parameter_constraints import ParameterConstraintError

        registry = _registry(
            constraints=(
                constraint(
                    "weights-sum",
                    ConstraintKind.SUM_TO_TARGET,
                    "weight_a",
                    "weight_b",
                    target=1.0,
                ),
            )
        )
        with self.assertRaisesRegex(ParameterConstraintError, "expected 1.0"):
            registry.register(
                {"weight_a": 0.4, "weight_b": 0.5},
                metric=0.5,
            )

    def test_readable(self) -> None:
        registry, _ = _registry().register({"weight_a": 0.4}, metric=0.5)
        summary = registry.readable()
        self.assertIn("trial registry 1/3", summary)
        self.assertIn("trial-1", summary)


class TrialRegistryValidationTests(unittest.TestCase):
    """Verify the registry rejects an invalid frozen context."""

    def test_empty_dataset_fingerprint_is_rejected(self) -> None:
        with self.assertRaisesRegex(TrialRegistrationError, "dataset fingerprint"):
            _registry(dataset_fingerprint="")

    def test_empty_code_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(TrialRegistrationError, "code version"):
            _registry(code_version="")

    def test_empty_trial_prefix_is_rejected(self) -> None:
        with self.assertRaisesRegex(TrialRegistrationError, "trial prefix"):
            _registry(trial_prefix="")

    def test_overlapping_train_validation_is_rejected(self) -> None:
        with self.assertRaisesRegex(TrialRegistrationError, "strictly before"):
            _registry(
                train_end=date(2021, 6, 30),
                validation_start=date(2021, 1, 1),
            )

    def test_reversed_validation_is_rejected(self) -> None:
        with self.assertRaisesRegex(TrialRegistrationError, "not be reversed"):
            _registry(
                validation_start=date(2022, 12, 31),
                validation_end=date(2021, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()
