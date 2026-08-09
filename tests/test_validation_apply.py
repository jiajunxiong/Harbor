"""Validation-period application tests (MVP 3 / SP 3.20).

Verifies that the validation period uses only the frozen training-fit snapshot
(SP 3.19) and the data knowable at the decision date: z-score scores a
validation value against the FITTED training mean/std, winsorize clips to the
FITTED bounds, industry scores use the FITTED baselines, symbols without a
baseline are recorded unapplied, and ``require_application_in_validation``
confines the fit to training and the application to the validation interval.
"""

import unittest
from datetime import date

from harbor.core.factor_standardization import (
    FactorDirection,
    StandardizationConfig,
    StandardizationMethod,
)
from harbor.core.training_fit import (
    IndustryBaseline,
    IndustryBaselineFit,
    StandardizationFit,
    TrainingFit,
    TrainingFitError,
)
from harbor.core.validation_apply import (
    AppliedIndustryBaseline,
    AppliedStandardization,
    ValidationApplication,
    ValidationApplyError,
    apply_fingerprint,
    apply_industry_baseline,
    apply_json,
    apply_standardization,
    build_validation_application,
    require_application_in_validation,
)
from harbor.core.validation_domain import EvaluationSplit

_FINGERPRINT = "f" * 64
_FIT_START = date(2019, 1, 1)
_FIT_END = date(2021, 12, 31)
_DECISION = date(2022, 3, 1)


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


def _stateless_fit(**overrides: object) -> StandardizationFit:
    """Return a stateless standardization fit (rank/plain quantile)."""
    fields: dict[str, object] = {
        "config": StandardizationConfig(method=StandardizationMethod.QUANTILE),
        "symbols": ("AAA", "BBB"),
        "observations": 10,
        "winsorize_lower": None,
        "winsorize_upper": None,
        "mean": None,
        "std": None,
    }
    fields.update(overrides)
    return StandardizationFit(**fields)  # type: ignore[arg-type]


def _industry_fit(**overrides: object) -> IndustryBaselineFit:
    """Return a valid fitted industry state with overridable fields."""
    fields: dict[str, object] = {
        "baselines": (
            IndustryBaseline(industry="bank", mean=5.0, std=2.0, observations=50),
            IndustryBaseline(industry="tech", mean=10.0, std=5.0, observations=40),
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


def _applied_std(**overrides: object) -> AppliedStandardization:
    """Return a valid applied-standardization record."""
    fields: dict[str, object] = {
        "decision_date": _DECISION,
        "scores": (("AAA", 2.0), ("BBB", -2.0)),
        "method": StandardizationMethod.ZSCORE,
    }
    fields.update(overrides)
    return AppliedStandardization(**fields)  # type: ignore[arg-type]


def _applied_industry(**overrides: object) -> AppliedIndustryBaseline:
    """Return a valid applied-industry record."""
    fields: dict[str, object] = {
        "decision_date": _DECISION,
        "scores": (("AAA", 2.0), ("BBB", 2.0)),
        "unapplied": (),
    }
    fields.update(overrides)
    return AppliedIndustryBaseline(**fields)  # type: ignore[arg-type]


def _application(**overrides: object) -> ValidationApplication:
    """Return a valid application record with overridable fields."""
    fields: dict[str, object] = {
        "fit_fingerprint": "fp-1",
        "decision_date": _DECISION,
        "dataset_fingerprint": _FINGERPRINT,
        "code_version": "1.0.0",
        "fingerprint": "app-1",
        "standardization": _applied_std(),
        "industry_baseline": None,
    }
    fields.update(overrides)
    return ValidationApplication(**fields)  # type: ignore[arg-type]


class AppliedStandardizationTests(unittest.TestCase):
    """Validates the :class:`AppliedStandardization` invariants."""

    def test_valid(self) -> None:
        applied = _applied_std()
        self.assertEqual(applied.decision_date, _DECISION)
        self.assertEqual(applied.scores, (("AAA", 2.0), ("BBB", -2.0)))
        self.assertEqual(applied.method, StandardizationMethod.ZSCORE)

    def test_unsorted_scores_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _applied_std(scores=(("BBB", -2.0), ("AAA", 2.0)))

    def test_duplicate_symbols_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _applied_std(scores=(("AAA", 2.0), ("AAA", 3.0)))

    def test_readable(self) -> None:
        self.assertIn("standardized 2022-03-01", _applied_std().readable())
        self.assertIn("zscore", _applied_std().readable())


class AppliedIndustryBaselineTests(unittest.TestCase):
    """Validates the :class:`AppliedIndustryBaseline` invariants."""

    def test_valid(self) -> None:
        applied = _applied_industry()
        self.assertEqual(applied.decision_date, _DECISION)
        self.assertEqual(applied.scores, (("AAA", 2.0), ("BBB", 2.0)))
        self.assertEqual(applied.unapplied, ())

    def test_unsorted_scores_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _applied_industry(scores=(("BBB", 2.0), ("AAA", 2.0)))

    def test_duplicate_symbols_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _applied_industry(scores=(("AAA", 2.0), ("AAA", 3.0)))

    def test_unsorted_unapplied_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _applied_industry(unapplied=("BBB", "AAA"))

    def test_readable(self) -> None:
        self.assertIn("industry-standardized", _applied_industry().readable())


class ValidationApplicationTests(unittest.TestCase):
    """Validates the persistable :class:`ValidationApplication` record."""

    def test_valid(self) -> None:
        app = _application()
        self.assertEqual(app.fit_fingerprint, "fp-1")
        self.assertEqual(app.decision_date, _DECISION)
        self.assertEqual(app.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(app.code_version, "1.0.0")
        self.assertEqual(app.fingerprint, "app-1")
        self.assertIsNotNone(app.standardization)

    def test_empty_fit_fingerprint_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _application(fit_fingerprint="")

    def test_empty_dataset_fingerprint_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _application(dataset_fingerprint="")

    def test_empty_code_version_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _application(code_version="")

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _application(fingerprint="")

    def test_no_applied_artifact_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            _application(standardization=None, industry_baseline=None)

    def test_readable(self) -> None:
        self.assertIn("applied fit fp-1", _application().readable())
        self.assertIn("fp app-1", _application().readable())


class ApplyStandardizationTests(unittest.TestCase):
    """Verifies z-score uses the FROZEN training stats (anti-lookahead)."""

    def test_zscore_scores_against_fitted_mean_and_std(self) -> None:
        # Validation cross-section [9, 1] has its own mean 5 / std 4; a
        # look-ahead score would be 1.0 / -1.0. The frozen fit (mean 5, std 2)
        # yields 2.0 / -2.0 instead — proving no validation re-fit.
        applied = apply_standardization(
            {"AAA": 9.0, "BBB": 1.0},
            fit=_std_fit(),
            decision_date=_DECISION,
        )
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], 2.0)
        self.assertAlmostEqual(scores["BBB"], -2.0)
        self.assertNotAlmostEqual(scores["AAA"], 1.0)

    def test_winsorize_clips_to_fitted_bounds(self) -> None:
        # The extreme validation value 100.0 must be clipped to the FITTED
        # upper bound 9.0 before z-scoring, so it scores (9-5)/2 = 2.0.
        applied = apply_standardization(
            {"AAA": 100.0, "BBB": 5.0},
            fit=_std_fit(),
            decision_date=_DECISION,
        )
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], 2.0)
        self.assertAlmostEqual(scores["BBB"], 0.0)

    def test_zscore_lower_is_better_negates(self) -> None:
        fit = _std_fit(
            config=StandardizationConfig(
                method=StandardizationMethod.ZSCORE,
                direction=FactorDirection.LOWER_IS_BETTER,
                winsorize=0.01,
            )
        )
        applied = apply_standardization({"AAA": 9.0, "BBB": 1.0}, fit=fit, decision_date=_DECISION)
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], -2.0)
        self.assertAlmostEqual(scores["BBB"], 2.0)

    def test_zscore_zero_std_yields_neutral(self) -> None:
        fit = _std_fit(mean=5.0, std=0.0)
        applied = apply_standardization({"AAA": 9.0, "BBB": 1.0}, fit=fit, decision_date=_DECISION)
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], 0.0)
        self.assertAlmostEqual(scores["BBB"], 0.0)

    def test_quantile_maps_validation_cross_section(self) -> None:
        # Plain quantile is stateless: it ranks the day's own values (data
        # available at the decision date) after any fitted clip.
        fit = _stateless_fit(config=StandardizationConfig(method=StandardizationMethod.QUANTILE))
        applied = apply_standardization({"AAA": 1.0, "BBB": 3.0}, fit=fit, decision_date=_DECISION)
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], 0.0)
        self.assertAlmostEqual(scores["BBB"], 1.0)
        self.assertEqual(applied.method, StandardizationMethod.QUANTILE)

    def test_quantile_lower_is_better_inverts(self) -> None:
        fit = _stateless_fit(
            config=StandardizationConfig(
                method=StandardizationMethod.QUANTILE,
                direction=FactorDirection.LOWER_IS_BETTER,
            )
        )
        applied = apply_standardization({"AAA": 1.0, "BBB": 3.0}, fit=fit, decision_date=_DECISION)
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], 1.0)
        self.assertAlmostEqual(scores["BBB"], 0.0)

    def test_rank_maps_validation_cross_section(self) -> None:
        fit = _stateless_fit(config=StandardizationConfig(method=StandardizationMethod.RANK))
        applied = apply_standardization({"AAA": 1.0, "BBB": 3.0}, fit=fit, decision_date=_DECISION)
        scores = dict(applied.scores)
        self.assertEqual(scores["AAA"], 2)
        self.assertEqual(scores["BBB"], 1)

    def test_missing_values_stay_none(self) -> None:
        applied = apply_standardization(
            {"AAA": 9.0, "BBB": None},
            fit=_std_fit(),
            decision_date=_DECISION,
        )
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], 2.0)
        self.assertIsNone(scores["BBB"])

    def test_empty_cross_section(self) -> None:
        applied = apply_standardization({}, fit=_std_fit(), decision_date=_DECISION)
        self.assertEqual(applied.scores, ())

    def test_records_decision_date(self) -> None:
        applied = apply_standardization({"AAA": 9.0}, fit=_std_fit(), decision_date=_DECISION)
        self.assertEqual(applied.decision_date, _DECISION)


class ApplyIndustryBaselineTests(unittest.TestCase):
    """Verifies industry scores use the FITTED baselines (anti-lookahead)."""

    def test_scores_against_fitted_baseline(self) -> None:
        applied = apply_industry_baseline(
            {"bank": {"AAA": 9.0}, "tech": {"BBB": 20.0}},
            fit=_industry_fit(),
            decision_date=_DECISION,
        )
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], 2.0)  # (9-5)/2
        self.assertAlmostEqual(scores["BBB"], 2.0)  # (20-10)/5
        self.assertEqual(applied.unapplied, ())

    def test_symbol_without_baseline_is_unapplied(self) -> None:
        applied = apply_industry_baseline(
            {"bank": {"AAA": 9.0}, "energy": {"DDD": 3.0}},
            fit=_industry_fit(),
            decision_date=_DECISION,
        )
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], 2.0)
        self.assertIsNone(scores["DDD"])
        self.assertEqual(applied.unapplied, ("DDD",))

    def test_excluded_industry_is_unapplied(self) -> None:
        fit = _industry_fit(
            excluded=("energy",),
            baselines=(
                IndustryBaseline(industry="bank", mean=5.0, std=2.0, observations=50),
                IndustryBaseline(industry="tech", mean=10.0, std=5.0, observations=40),
            ),
        )
        applied = apply_industry_baseline(
            {"energy": {"DDD": 3.0}}, fit=fit, decision_date=_DECISION
        )
        self.assertIsNone(dict(applied.scores)["DDD"])
        self.assertEqual(applied.unapplied, ("DDD",))

    def test_zero_std_baseline_yields_neutral(self) -> None:
        fit = _industry_fit(
            baselines=(IndustryBaseline(industry="bank", mean=5.0, std=0.0, observations=50),),
            symbols=("AAA",),
        )
        applied = apply_industry_baseline({"bank": {"AAA": 9.0}}, fit=fit, decision_date=_DECISION)
        self.assertAlmostEqual(dict(applied.scores)["AAA"], 0.0)

    def test_missing_values_stay_none(self) -> None:
        applied = apply_industry_baseline(
            {"bank": {"AAA": 9.0, "BBB": None}},
            fit=_industry_fit(),
            decision_date=_DECISION,
        )
        scores = dict(applied.scores)
        self.assertAlmostEqual(scores["AAA"], 2.0)
        self.assertIsNone(scores["BBB"])

    def test_records_decision_date(self) -> None:
        applied = apply_industry_baseline(
            {"bank": {"AAA": 9.0}}, fit=_industry_fit(), decision_date=_DECISION
        )
        self.assertEqual(applied.decision_date, _DECISION)


class ApplicationFingerprintTests(unittest.TestCase):
    """Verifies the application fingerprint is stable and re-derivable."""

    def test_fingerprint_stable_for_equal_applications(self) -> None:
        self.assertEqual(apply_fingerprint(_application()), apply_fingerprint(_application()))

    def test_fingerprint_changes_with_decision_date(self) -> None:
        changed = _application(decision_date=date(2022, 4, 1))
        self.assertNotEqual(apply_fingerprint(_application()), apply_fingerprint(changed))

    def test_fingerprint_changes_with_fit_fingerprint(self) -> None:
        changed = _application(fit_fingerprint="fp-2")
        self.assertNotEqual(apply_fingerprint(_application()), apply_fingerprint(changed))

    def test_fingerprint_changes_with_dataset_fingerprint(self) -> None:
        changed = _application(dataset_fingerprint="g" * 64)
        self.assertNotEqual(apply_fingerprint(_application()), apply_fingerprint(changed))

    def test_fingerprint_changes_with_code_version(self) -> None:
        changed = _application(code_version="1.0.1")
        self.assertNotEqual(apply_fingerprint(_application()), apply_fingerprint(changed))

    def test_fingerprint_changes_with_scores(self) -> None:
        changed = _application(standardization=_applied_std(scores=(("AAA", 3.0), ("BBB", -2.0))))
        self.assertNotEqual(apply_fingerprint(_application()), apply_fingerprint(changed))

    def test_build_records_rederivable_fingerprint(self) -> None:
        applied = apply_standardization(
            {"AAA": 9.0, "BBB": 1.0}, fit=_std_fit(), decision_date=_DECISION
        )
        app = build_validation_application(
            fit=_fit(),
            decision_date=_DECISION,
            standardization=applied,
        )
        self.assertEqual(app.fingerprint, apply_fingerprint(app))
        self.assertEqual(len(app.fingerprint), 64)
        self.assertEqual(app.fit_fingerprint, _fit().fingerprint)
        self.assertEqual(app.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(app.code_version, "1.0.0")

    def test_build_rejects_no_artifact(self) -> None:
        with self.assertRaises(ValidationApplyError):
            build_validation_application(fit=_fit(), decision_date=_DECISION)

    def test_build_rejects_standardization_when_fit_has_none(self) -> None:
        fit = _fit(standardization=None, fitted_state=(("tail_factor", 0.5),))
        with self.assertRaises(ValidationApplyError):
            build_validation_application(
                fit=fit,
                decision_date=_DECISION,
                standardization=_applied_std(),
            )

    def test_build_rejects_industry_when_fit_has_none(self) -> None:
        fit = _fit(industry_baseline=None)
        with self.assertRaises(ValidationApplyError):
            build_validation_application(
                fit=fit,
                decision_date=_DECISION,
                industry_baseline=_applied_industry(),
            )

    def test_build_rejects_date_mismatch(self) -> None:
        with self.assertRaises(ValidationApplyError):
            build_validation_application(
                fit=_fit(),
                decision_date=_DECISION,
                standardization=_applied_std(decision_date=date(2022, 4, 1)),
            )

    def test_json_is_key_sorted_and_stable(self) -> None:
        self.assertEqual(apply_json(_application()), apply_json(_application()))
        self.assertIn('"fit_fingerprint":"fp-1"', apply_json(_application()))


class ApplicationWithinValidationTests(unittest.TestCase):
    """Verifies :func:`require_application_in_validation` (SP 3.4 tie)."""

    def test_application_in_validation_passes(self) -> None:
        require_application_in_validation(_DECISION, _fit(), _split())

    def test_application_at_validation_start_passes(self) -> None:
        require_application_in_validation(date(2022, 1, 3), _fit(), _split())

    def test_application_at_validation_end_passes(self) -> None:
        require_application_in_validation(date(2022, 12, 30), _fit(), _split())

    def test_application_in_training_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            require_application_in_validation(date(2021, 12, 31), _fit(), _split())

    def test_application_in_test_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            require_application_in_validation(date(2023, 1, 2), _fit(), _split())

    def test_application_before_validation_rejected(self) -> None:
        with self.assertRaises(ValidationApplyError):
            require_application_in_validation(date(2022, 1, 2), _fit(), _split())

    def test_fit_extending_into_validation_rejected(self) -> None:
        # A fit that was re-fitted on validation data is rejected before the
        # date check — the SP 3.19 confinement fails first.
        fit = _fit(fit_end=date(2022, 6, 1))
        with self.assertRaises(TrainingFitError):
            require_application_in_validation(_DECISION, fit, _split())


if __name__ == "__main__":
    unittest.main()
