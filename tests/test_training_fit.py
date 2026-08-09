"""Training-period fit and persistable snapshot tests (MVP 3 / SP 3.19).

Verifies that factor-standardization thresholds, industry baselines and other
fitted state are computed from the training period only, that the fitted
state is persisted as a deterministic, fingerprinted snapshot, and that the
snapshot can be confined to the SP 3.4 training interval
(``require_fit_within_training``) so validation/test application can replay
the frozen fit without re-fitting (SP 3.20).
"""

import math
import unittest
from datetime import date

from harbor.core.factor_standardization import (
    StandardizationConfig,
    StandardizationMethod,
)
from harbor.core.training_fit import (
    IndustryBaseline,
    IndustryBaselineFit,
    StandardizationFit,
    TrainingFit,
    TrainingFitError,
    build_training_fit,
    fit_fingerprint,
    fit_json,
    industry_baseline_fit,
    require_fit_within_training,
    standardization_fit,
)
from harbor.core.validation_domain import EvaluationSplit

_FINGERPRINT = "f" * 64
_FIT_START = date(2019, 1, 1)
_FIT_END = date(2021, 12, 31)


def _std_fit(**overrides: object) -> StandardizationFit:
    """Return a valid fitted standardization state with overridable fields."""
    fields: dict[str, object] = {
        "config": StandardizationConfig(method=StandardizationMethod.ZSCORE, winsorize=0.01),
        "symbols": ("AAA", "BBB"),
        "observations": 10,
        "winsorize_lower": 1.0,
        "winsorize_upper": 9.0,
        "mean": 5.0,
        "std": 2.0,
    }
    fields.update(overrides)
    return StandardizationFit(**fields)  # type: ignore[arg-type]


def _industry_fit(**overrides: object) -> IndustryBaselineFit:
    """Return a valid fitted industry state with overridable fields."""
    fields: dict[str, object] = {
        "baselines": (
            IndustryBaseline(industry="bank", mean=0.1, std=0.02, observations=50),
            IndustryBaseline(industry="tech", mean=0.2, std=0.03, observations=40),
        ),
        "symbols": ("AAA", "BBB", "CCC"),
        "minimum_observations": 1,
        "excluded": (),
    }
    fields.update(overrides)
    return IndustryBaselineFit(**fields)  # type: ignore[arg-type]


def _fit(**overrides: object) -> TrainingFit:
    """Return a valid fit snapshot with overridable fields."""
    fields: dict[str, object] = {
        "fit_start": _FIT_START,
        "fit_end": _FIT_END,
        "dataset_fingerprint": _FINGERPRINT,
        "code_version": "1.0.0",
        "fingerprint": "fp-1",
        "standardization": _std_fit(),
        "industry_baseline": None,
        "fitted_state": (("tail_factor", 0.5),),
    }
    fields.update(overrides)
    return TrainingFit(**fields)  # type: ignore[arg-type]


def _split(**overrides: object) -> EvaluationSplit:
    """Return a valid split with overridable boundaries."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 3),
        "validation_end": date(2022, 12, 30),
        "test_start": date(2023, 1, 2),
        "test_end": date(2024, 12, 31),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


class StandardizationFitTests(unittest.TestCase):
    """Validates the :class:`StandardizationFit` invariants."""

    def test_valid_zscore_winsorized(self) -> None:
        fit = _std_fit()
        self.assertEqual(fit.symbols, ("AAA", "BBB"))
        self.assertEqual(fit.observations, 10)
        self.assertEqual(fit.winsorize_lower, 1.0)
        self.assertEqual(fit.winsorize_upper, 9.0)
        self.assertAlmostEqual(fit.mean, 5.0)
        self.assertAlmostEqual(fit.std, 2.0)

    def test_empty_symbols_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _std_fit(symbols=())

    def test_non_positive_observations_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _std_fit(observations=0)

    def test_winsorize_thresholds_must_be_paired(self) -> None:
        with self.assertRaises(TrainingFitError):
            _std_fit(winsorize_upper=None)

    def test_winsorize_lower_below_upper(self) -> None:
        with self.assertRaises(TrainingFitError):
            _std_fit(winsorize_lower=9.0, winsorize_upper=1.0)

    def test_mean_and_std_must_be_paired(self) -> None:
        with self.assertRaises(TrainingFitError):
            _std_fit(std=None)

    def test_negative_std_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _std_fit(mean=1.0, std=-1.0)

    def test_winsorizing_config_requires_thresholds(self) -> None:
        with self.assertRaises(TrainingFitError):
            _std_fit(winsorize_lower=None, winsorize_upper=None)

    def test_zscore_config_requires_mean_and_std(self) -> None:
        with self.assertRaises(TrainingFitError):
            _std_fit(
                config=StandardizationConfig(method=StandardizationMethod.ZSCORE),
                mean=None,
                std=None,
            )

    def test_stateless_config_allows_no_numeric_state(self) -> None:
        fit = _std_fit(
            config=StandardizationConfig(method=StandardizationMethod.RANK),
            winsorize_lower=None,
            winsorize_upper=None,
            mean=None,
            std=None,
        )
        self.assertIsNone(fit.mean)
        self.assertIsNone(fit.std)

    def test_readable(self) -> None:
        self.assertIn("standardization zscore", _std_fit().readable())
        self.assertIn("obs 10", _std_fit().readable())


class StandardizationFitFunctionTests(unittest.TestCase):
    """Verifies :func:`standardization_fit` pools only training values."""

    _VALUES = {
        date(2019, 1, 1): {"AAA": 1.0, "BBB": 5.0, "CCC": None},
        date(2019, 1, 2): {"AAA": 3.0, "BBB": 9.0},
    }

    def test_winsorize_bounds_from_pooled_training_values(self) -> None:
        config = StandardizationConfig(method=StandardizationMethod.QUANTILE, winsorize=0.25)
        fit = standardization_fit(self._VALUES, config=config)
        self.assertEqual(fit.symbols, ("AAA", "BBB"))
        self.assertEqual(fit.observations, 4)
        self.assertAlmostEqual(fit.winsorize_lower, 3.0)
        self.assertAlmostEqual(fit.winsorize_upper, 5.0)
        self.assertIsNone(fit.mean)
        self.assertIsNone(fit.std)

    def test_zscore_fits_pooled_mean_and_std(self) -> None:
        config = StandardizationConfig(method=StandardizationMethod.ZSCORE)
        fit = standardization_fit(self._VALUES, config=config)
        self.assertAlmostEqual(fit.mean, 4.5)
        self.assertAlmostEqual(fit.std, math.sqrt(8.75))
        self.assertIsNone(fit.winsorize_lower)

    def test_stateless_rank_records_training_evidence_only(self) -> None:
        config = StandardizationConfig(method=StandardizationMethod.RANK)
        fit = standardization_fit(self._VALUES, config=config)
        self.assertEqual(fit.symbols, ("AAA", "BBB"))
        self.assertEqual(fit.observations, 4)
        self.assertIsNone(fit.winsorize_lower)
        self.assertIsNone(fit.mean)
        self.assertIsNone(fit.std)

    def test_missing_values_never_participate(self) -> None:
        config = StandardizationConfig(method=StandardizationMethod.QUANTILE, winsorize=0.25)
        fit = standardization_fit({date(2019, 1, 1): {"AAA": 1.0, "BBB": None}}, config=config)
        self.assertEqual(fit.symbols, ("AAA",))
        self.assertEqual(fit.observations, 1)
        self.assertAlmostEqual(fit.winsorize_lower, 1.0)
        self.assertAlmostEqual(fit.winsorize_upper, 1.0)

    def test_single_value_yields_single_threshold(self) -> None:
        config = StandardizationConfig(method=StandardizationMethod.QUANTILE, winsorize=0.25)
        fit = standardization_fit({date(2019, 1, 1): {"AAA": 2.0}}, config=config)
        self.assertEqual(fit.observations, 1)
        self.assertAlmostEqual(fit.winsorize_lower, 2.0)
        self.assertAlmostEqual(fit.winsorize_upper, 2.0)

    def test_empty_training_period_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            standardization_fit({}, config=StandardizationConfig())

    def test_all_missing_values_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            standardization_fit(
                {date(2019, 1, 1): {"AAA": None, "BBB": None}},
                config=StandardizationConfig(),
            )


class IndustryBaselineTests(unittest.TestCase):
    """Validates the :class:`IndustryBaseline` invariants."""

    def test_valid(self) -> None:
        baseline = IndustryBaseline(industry="bank", mean=0.1, std=0.02, observations=50)
        self.assertEqual(baseline.industry, "bank")
        self.assertAlmostEqual(baseline.mean, 0.1)
        self.assertAlmostEqual(baseline.std, 0.02)
        self.assertEqual(baseline.observations, 50)

    def test_empty_industry_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            IndustryBaseline(industry="", mean=0.1, std=0.02, observations=1)

    def test_non_positive_observations_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            IndustryBaseline(industry="bank", mean=0.1, std=0.02, observations=0)

    def test_negative_std_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            IndustryBaseline(industry="bank", mean=0.1, std=-0.02, observations=1)

    def test_readable(self) -> None:
        baseline = IndustryBaseline(industry="bank", mean=0.1, std=0.02, observations=50)
        self.assertIn("bank", baseline.readable())
        self.assertIn("n=50", baseline.readable())


class IndustryBaselineFitTests(unittest.TestCase):
    """Validates the :class:`IndustryBaselineFit` invariants."""

    def test_valid(self) -> None:
        fit = _industry_fit()
        self.assertEqual(fit.minimum_observations, 1)
        self.assertEqual(fit.symbols, ("AAA", "BBB", "CCC"))
        self.assertEqual(fit.excluded, ())

    def test_empty_baselines_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _industry_fit(baselines=())

    def test_empty_symbols_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _industry_fit(symbols=())

    def test_non_positive_minimum_observations_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _industry_fit(minimum_observations=0)

    def test_unsorted_baselines_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _industry_fit(
                baselines=(
                    IndustryBaseline(industry="tech", mean=0.2, std=0.03, observations=40),
                    IndustryBaseline(industry="bank", mean=0.1, std=0.02, observations=50),
                )
            )

    def test_duplicate_baselines_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _industry_fit(
                baselines=(
                    IndustryBaseline(industry="bank", mean=0.1, std=0.02, observations=50),
                    IndustryBaseline(industry="bank", mean=0.2, std=0.03, observations=40),
                )
            )

    def test_readable(self) -> None:
        self.assertIn("bank", _industry_fit().readable())
        self.assertIn("tech", _industry_fit().readable())


class IndustryBaselineFitFunctionTests(unittest.TestCase):
    """Verifies :func:`industry_baseline_fit` pools per-industry training values."""

    _VALUES = {
        "tech": {"AAA": 10.0, "BBB": 20.0, "CCC": None},
        "bank": {"AAA": 5.0},
        "energy": {"DDD": 1.0},
    }

    def test_fits_all_industries_sorted(self) -> None:
        fit = industry_baseline_fit(self._VALUES)
        self.assertEqual([b.industry for b in fit.baselines], ["bank", "energy", "tech"])
        bank, energy, tech = fit.baselines
        self.assertAlmostEqual(bank.mean, 5.0)
        self.assertAlmostEqual(bank.std, 0.0)
        self.assertEqual(bank.observations, 1)
        self.assertAlmostEqual(energy.mean, 1.0)
        self.assertEqual(energy.observations, 1)
        self.assertAlmostEqual(tech.mean, 15.0)
        self.assertAlmostEqual(tech.std, 5.0)
        self.assertEqual(tech.observations, 2)
        self.assertEqual(fit.symbols, ("AAA", "BBB", "DDD"))
        self.assertEqual(fit.excluded, ())

    def test_excludes_industries_below_minimum_observations(self) -> None:
        fit = industry_baseline_fit(self._VALUES, minimum_observations=2)
        self.assertEqual([b.industry for b in fit.baselines], ["tech"])
        self.assertAlmostEqual(fit.baselines[0].mean, 15.0)
        # Symbols records the full training universe, including symbols that
        # belonged to an excluded (thin) industry.
        self.assertEqual(fit.symbols, ("AAA", "BBB", "DDD"))
        self.assertEqual(fit.excluded, ("bank", "energy"))

    def test_non_positive_minimum_observations_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            industry_baseline_fit(self._VALUES, minimum_observations=0)

    def test_all_industries_below_threshold_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            industry_baseline_fit(self._VALUES, minimum_observations=5)

    def test_empty_input_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            industry_baseline_fit({})


class TrainingFitTests(unittest.TestCase):
    """Validates the persistable :class:`TrainingFit` snapshot."""

    def test_valid(self) -> None:
        fit = _fit()
        self.assertEqual(fit.fit_start, _FIT_START)
        self.assertEqual(fit.fit_end, _FIT_END)
        self.assertEqual(fit.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(fit.code_version, "1.0.0")
        self.assertEqual(fit.fingerprint, "fp-1")
        self.assertIsNotNone(fit.standardization)
        self.assertEqual(fit.fitted_state, (("tail_factor", 0.5),))

    def test_reversed_range_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _fit(fit_start=date(2022, 1, 1), fit_end=date(2021, 12, 31))

    def test_empty_dataset_fingerprint_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _fit(dataset_fingerprint="")

    def test_empty_code_version_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _fit(code_version="")

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _fit(fingerprint="")

    def test_no_fitted_artifact_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _fit(standardization=None, industry_baseline=None, fitted_state=())

    def test_duplicate_fitted_state_names_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _fit(fitted_state=(("a", 1.0), ("a", 2.0)))

    def test_empty_fitted_state_name_rejected(self) -> None:
        with self.assertRaises(TrainingFitError):
            _fit(fitted_state=(("", 1.0),))

    def test_readable(self) -> None:
        readable = _fit().readable()
        self.assertIn("fit 2019-01-01..2021-12-31", readable)
        self.assertIn("standardization", readable)
        self.assertIn("fp-1", readable)


class TrainingFitFingerprintTests(unittest.TestCase):
    """Verifies the snapshot fingerprint is stable and re-derivable."""

    def test_fingerprint_stable_for_equal_snapshots(self) -> None:
        self.assertEqual(fit_fingerprint(_fit()), fit_fingerprint(_fit()))

    def test_fingerprint_changes_with_boundaries(self) -> None:
        changed = _fit(fit_start=date(2019, 6, 1))
        self.assertNotEqual(fit_fingerprint(_fit()), fit_fingerprint(changed))

    def test_fingerprint_changes_with_dataset_fingerprint(self) -> None:
        changed = _fit(dataset_fingerprint="g" * 64)
        self.assertNotEqual(fit_fingerprint(_fit()), fit_fingerprint(changed))

    def test_fingerprint_changes_with_code_version(self) -> None:
        changed = _fit(code_version="1.0.1")
        self.assertNotEqual(fit_fingerprint(_fit()), fit_fingerprint(changed))

    def test_fingerprint_changes_with_standardization(self) -> None:
        changed = _fit(standardization=_std_fit(mean=6.0))
        self.assertNotEqual(fit_fingerprint(_fit()), fit_fingerprint(changed))

    def test_fingerprint_changes_with_industry_baseline(self) -> None:
        changed = _fit(standardization=None, industry_baseline=_industry_fit())
        self.assertNotEqual(fit_fingerprint(_fit()), fit_fingerprint(changed))

    def test_fingerprint_changes_with_fitted_state(self) -> None:
        changed = _fit(fitted_state=(("tail_factor", 0.6),))
        self.assertNotEqual(fit_fingerprint(_fit()), fit_fingerprint(changed))

    def test_fingerprint_excludes_derived_fingerprint(self) -> None:
        # The digest excludes the derived fingerprint field, so re-deriving
        # against the built snapshot reproduces the recorded value.
        snapshot = build_training_fit(
            fit_start=_FIT_START,
            fit_end=_FIT_END,
            dataset_fingerprint=_FINGERPRINT,
            code_version="1.0.0",
            standardization=_std_fit(),
            fitted_state=(("tail_factor", 0.5),),
        )
        self.assertEqual(snapshot.fingerprint, fit_fingerprint(snapshot))
        self.assertEqual(len(snapshot.fingerprint), 64)

    def test_build_normalizes_fitted_state_order(self) -> None:
        snapshot = build_training_fit(
            fit_start=_FIT_START,
            fit_end=_FIT_END,
            dataset_fingerprint=_FINGERPRINT,
            code_version="1.0.0",
            fitted_state=(("beta", 2.0), ("alpha", 1.0)),
        )
        self.assertEqual(snapshot.fitted_state, (("alpha", 1.0), ("beta", 2.0)))

    def test_build_rejects_duplicate_fitted_state(self) -> None:
        with self.assertRaises(TrainingFitError):
            build_training_fit(
                fit_start=_FIT_START,
                fit_end=_FIT_END,
                dataset_fingerprint=_FINGERPRINT,
                code_version="1.0.0",
                fitted_state=(("alpha", 1.0), ("alpha", 2.0)),
            )

    def test_json_is_key_sorted_and_stable(self) -> None:
        self.assertEqual(fit_json(_fit()), fit_json(_fit()))
        self.assertIn('"fit_start":"2019-01-01"', fit_json(_fit()))


class FitWithinTrainingTests(unittest.TestCase):
    """Verifies :func:`require_fit_within_training` confines the fit to training."""

    def test_fit_subset_of_training_passes(self) -> None:
        fit = _fit(fit_start=date(2019, 6, 1), fit_end=date(2021, 6, 30))
        require_fit_within_training(fit, _split())

    def test_fit_equal_to_training_passes(self) -> None:
        fit = _fit(fit_start=date(2019, 1, 1), fit_end=date(2021, 12, 31))
        require_fit_within_training(fit, _split())

    def test_fit_starting_before_training_rejected(self) -> None:
        fit = _fit(fit_start=date(2018, 12, 1), fit_end=date(2021, 12, 31))
        with self.assertRaises(TrainingFitError):
            require_fit_within_training(fit, _split())

    def test_fit_ending_after_training_rejected(self) -> None:
        fit = _fit(fit_start=date(2019, 1, 1), fit_end=date(2022, 1, 3))
        with self.assertRaises(TrainingFitError):
            require_fit_within_training(fit, _split())


if __name__ == "__main__":
    unittest.main()
