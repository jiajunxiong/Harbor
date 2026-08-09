"""Pre-registered baseline and comparison tests (MVP 3 / SP 3.30).

Verifies that the baseline configuration and baseline metric are fixed before
parameter search (预注册基线), validated against the SP 3.15 space, and that
the comparison report contrasts the baseline against the selected parameters —
per-parameter differences, the metric gap and the direction-aware improvement —
exposing any baseline that was silently re-registered to match the selection.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    ParameterSpaceError,
    UndeclaredParameterError,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.pre_registered_baseline import (
    BaselineComparison,
    ParameterDifference,
    PreRegisteredBaseline,
    PreRegisteredBaselineError,
    baseline_fingerprint,
    baseline_json,
    compare_baseline_selection,
    comparison_fingerprint,
    comparison_json,
    pre_register_baseline,
)
from harbor.core.validation_config import MetricDirection
from harbor.core.validation_domain import Parameter, ParameterTrial

_FINGERPRINT = "f" * 64
_BASELINE_PARAMS = {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 252}


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
        "train_end": date(2020, 12, 31),
        "validation_start": date(2021, 1, 1),
        "validation_end": date(2022, 12, 31),
        "seed": 42,
        "code_version": "1.0.0",
        "metric": 0.18,
    }
    fields.update(overrides)
    return ParameterTrial(**fields)  # type: ignore[arg-type]


def _baseline(**overrides: object) -> PreRegisteredBaseline:
    """Return a pre-registered baseline with overridable fields."""
    fields: dict[str, object] = {
        "space": _space(),
        "parameters": dict(_BASELINE_PARAMS),
        "metric_name": "sharpe",
        "metric": 0.10,
        "dataset_fingerprint": _FINGERPRINT,
        "code_version": "1.0.0",
    }
    fields.update(overrides)
    return pre_register_baseline(**fields)  # type: ignore[arg-type]


def _difference(**overrides: object) -> ParameterDifference:
    """Return a valid parameter difference with overridable fields."""
    fields: dict[str, object] = {
        "name": "lookback",
        "baseline_value": 252,
        "selected_value": 324,
        "changed": True,
    }
    fields.update(overrides)
    return ParameterDifference(**fields)  # type: ignore[arg-type]


def _comparison(**overrides: object) -> BaselineComparison:
    """Return a valid comparison report with overridable fields."""
    fields: dict[str, object] = {
        "baseline": _baseline(),
        "selected_parameters": _trial().parameters,
        "differences": (_difference(),),
        "baseline_metric": 0.10,
        "selected_metric": 0.18,
        "metric_gap": 0.08,
        "improved": True,
        "fingerprint": "cmp-1",
    }
    fields.update(overrides)
    return BaselineComparison(**fields)  # type: ignore[arg-type]


class PreRegisterBaselineTests(unittest.TestCase):
    """Verifies :func:`pre_register_baseline` fixes the baseline."""

    def test_registers_fixed_baseline(self) -> None:
        baseline = _baseline()
        self.assertEqual(baseline.metric_name, "sharpe")
        self.assertEqual(baseline.metric, 0.10)
        names = [parameter.name for parameter in baseline.parameters]
        self.assertEqual(names, ["cash_weight", "factor_weight", "lookback"])

    def test_validates_against_space(self) -> None:
        with self.assertRaises(UndeclaredParameterError):
            _baseline(parameters={"phantom": 0.5})
        with self.assertRaises(ParameterSpaceError):
            _baseline(parameters={"cash_weight": 2.0})

    def test_fingerprint_rederivable(self) -> None:
        baseline = _baseline()
        self.assertEqual(baseline.fingerprint, baseline_fingerprint(baseline))
        self.assertEqual(len(baseline.fingerprint), 64)

    def test_readable(self) -> None:
        self.assertIn("baseline [cash_weight=0.05", _baseline().readable())
        self.assertIn("metric sharpe 0.1", _baseline().readable())


class BaselineValidationTests(unittest.TestCase):
    """Validates the :class:`PreRegisteredBaseline` invariants."""

    def _direct(self, **overrides: object) -> PreRegisteredBaseline:
        fields: dict[str, object] = {
            "parameters": (
                Parameter(name="cash_weight", value=0.05),
                Parameter(name="lookback", value=252),
            ),
            "metric_name": "sharpe",
            "metric": 0.10,
            "dataset_fingerprint": _FINGERPRINT,
            "code_version": "1.0.0",
            "fingerprint": "fp",
        }
        fields.update(overrides)
        return PreRegisteredBaseline(**fields)  # type: ignore[arg-type]

    def test_valid(self) -> None:
        baseline = self._direct()
        self.assertEqual(len(baseline.parameters), 2)

    def test_empty_parameters_rejected(self) -> None:
        with self.assertRaises(PreRegisteredBaselineError):
            self._direct(parameters=())

    def test_unsorted_parameters_rejected(self) -> None:
        with self.assertRaises(PreRegisteredBaselineError):
            self._direct(
                parameters=(
                    Parameter(name="lookback", value=252),
                    Parameter(name="cash_weight", value=0.05),
                )
            )

    def test_duplicate_parameters_rejected(self) -> None:
        with self.assertRaises(PreRegisteredBaselineError):
            self._direct(
                parameters=(
                    Parameter(name="cash_weight", value=0.05),
                    Parameter(name="cash_weight", value=0.10),
                )
            )

    def test_empty_metric_name_rejected(self) -> None:
        with self.assertRaises(PreRegisteredBaselineError):
            self._direct(metric_name="   ")

    def test_empty_dataset_fingerprint_rejected(self) -> None:
        with self.assertRaises(PreRegisteredBaselineError):
            self._direct(dataset_fingerprint="")

    def test_empty_code_version_rejected(self) -> None:
        with self.assertRaises(PreRegisteredBaselineError):
            self._direct(code_version="")

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(PreRegisteredBaselineError):
            self._direct(fingerprint="")


class CompareBaselineSelectionTests(unittest.TestCase):
    """Verifies :func:`compare_baseline_selection` contrasts baseline vs selected."""

    def test_reports_parameter_differences(self) -> None:
        baseline = _baseline()
        # The selected trial varies lookback 252 -> 324 while the weights stay.
        selected = _trial(
            trial_id="trial-3",
            parameters=(
                Parameter(name="cash_weight", value=0.05),
                Parameter(name="factor_weight", value=0.95),
                Parameter(name="lookback", value=324),
            ),
            metric=0.18,
        )
        comparison = compare_baseline_selection(baseline, selected)
        by_name = {difference.name: difference for difference in comparison.differences}
        self.assertFalse(by_name["cash_weight"].changed)
        self.assertFalse(by_name["factor_weight"].changed)
        self.assertTrue(by_name["lookback"].changed)
        self.assertEqual(by_name["lookback"].baseline_value, 252)
        self.assertEqual(by_name["lookback"].selected_value, 324)

    def test_reports_metric_gap_and_improved(self) -> None:
        baseline = _baseline()
        selected = _trial(metric=0.18)
        comparison = compare_baseline_selection(baseline, selected)
        self.assertEqual(comparison.baseline_metric, 0.10)
        self.assertEqual(comparison.selected_metric, 0.18)
        self.assertAlmostEqual(comparison.metric_gap, 0.08)
        self.assertTrue(comparison.improved)

    def test_lower_better_direction(self) -> None:
        baseline = _baseline(metric=0.20)
        selected = _trial(metric=0.12)
        comparison = compare_baseline_selection(
            baseline, selected, direction=MetricDirection.LOWER_BETTER
        )
        self.assertAlmostEqual(comparison.metric_gap, -0.08)
        self.assertTrue(comparison.improved)

    def test_not_improved_when_below_baseline(self) -> None:
        baseline = _baseline(metric=0.30)
        selected = _trial(metric=0.18)
        comparison = compare_baseline_selection(baseline, selected)
        self.assertFalse(comparison.improved)

    def test_no_selection(self) -> None:
        baseline = _baseline()
        comparison = compare_baseline_selection(baseline, None)
        self.assertEqual(comparison.selected_parameters, ())
        self.assertIsNone(comparison.selected_metric)
        self.assertIsNone(comparison.metric_gap)
        self.assertIsNone(comparison.improved)
        # Every baseline parameter is absent from the selection → changed.
        self.assertTrue(all(difference.changed for difference in comparison.differences))

    def test_tampered_baseline_is_exposed(self) -> None:
        # A baseline re-registered AFTER the search to match the selected
        # parameters yields all-unchanged differences and no improvement.
        selected = _trial(
            parameters=(
                Parameter(name="cash_weight", value=0.05),
                Parameter(name="factor_weight", value=0.95),
                Parameter(name="lookback", value=324),
            ),
            metric=0.18,
        )
        tampered = _baseline(
            parameters={"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 324},
            metric=0.18,
        )
        comparison = compare_baseline_selection(tampered, selected)
        self.assertTrue(all(not difference.changed for difference in comparison.differences))
        self.assertFalse(comparison.improved)
        self.assertAlmostEqual(comparison.metric_gap, 0.0)

    def test_absent_parameter_flagged(self) -> None:
        baseline = _baseline()
        # A selection that drops factor_weight: that parameter is absent.
        selected = _trial(
            parameters=(
                Parameter(name="cash_weight", value=0.05),
                Parameter(name="lookback", value=252),
            ),
            metric=0.15,
        )
        comparison = compare_baseline_selection(baseline, selected)
        by_name = {difference.name: difference for difference in comparison.differences}
        self.assertIsNone(by_name["factor_weight"].selected_value)
        self.assertTrue(by_name["factor_weight"].changed)

    def test_readable(self) -> None:
        baseline = _baseline()
        selected = _trial(metric=0.18)
        comparison = compare_baseline_selection(baseline, selected)
        self.assertIn("baseline comparison", comparison.readable())
        self.assertIn("improved True", comparison.readable())


class ComparisonFingerprintTests(unittest.TestCase):
    """Verifies the comparison fingerprint is stable and sensitive."""

    def _comparison(self) -> BaselineComparison:
        return compare_baseline_selection(_baseline(), _trial(metric=0.18))

    def test_fingerprint_rederivable(self) -> None:
        comparison = self._comparison()
        self.assertEqual(comparison.fingerprint, comparison_fingerprint(comparison))
        self.assertEqual(len(comparison.fingerprint), 64)

    def test_fingerprint_stable_for_equal(self) -> None:
        self.assertEqual(
            comparison_fingerprint(self._comparison()),
            comparison_fingerprint(self._comparison()),
        )

    def test_fingerprint_changes_with_selected(self) -> None:
        changed = compare_baseline_selection(_baseline(), _trial(metric=0.25))
        self.assertNotEqual(
            comparison_fingerprint(self._comparison()),
            comparison_fingerprint(changed),
        )

    def test_fingerprint_changes_with_baseline(self) -> None:
        changed = compare_baseline_selection(_baseline(metric=0.30), _trial(metric=0.18))
        self.assertNotEqual(
            comparison_fingerprint(self._comparison()),
            comparison_fingerprint(changed),
        )

    def test_json_is_key_sorted_and_stable(self) -> None:
        self.assertEqual(
            comparison_json(self._comparison()),
            comparison_json(self._comparison()),
        )
        self.assertIn('"baseline_metric":0.1', comparison_json(self._comparison()))
        self.assertIn('"metric_name":"sharpe"', baseline_json(_baseline()))


if __name__ == "__main__":
    unittest.main()
