"""Validation-domain type tests (MVP 3 / SP 3.1).

Verifies the immutable value types that give out-of-sample validation its
shared vocabulary: the validation status enum (SP 3.13), the OOS conclusion
enum (SP 3.58), the frozen dataset manifest (SP 3.6), the train / validation
/ test split (SP 3.4), the parameter trial (SP 3.18) and the walk-forward
fold (SP 3.31). Every type is immutable and the split/fold types reject any
reversed, overlapping or empty range with :class:`SplitBoundaryError` instead
of silently normalizing it (SP 3.4).
"""

import unittest
from dataclasses import FrozenInstanceError
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.validation_domain import (
    DatasetManifest,
    EvaluationSplit,
    OOSConclusion,
    Parameter,
    ParameterTrial,
    SplitBoundaryError,
    ValidationStatus,
    WalkForwardFold,
)


def _manifest(**overrides: object) -> DatasetManifest:
    """Return a valid dataset manifest with overridable fields."""
    fields: dict[str, object] = {
        "markets": (Market.HK, Market.US),
        "base_currency": Currency.HKD,
        "start_date": date(2019, 1, 1),
        "end_date": date(2024, 12, 31),
        "data_cutoff": date(2024, 12, 31),
        "config_hash": "abc123",
        "code_version": "1.0.0",
        "calendar_version": "hkex-2024",
        "fx_source": "mock",
        "fingerprint": "fp-1",
    }
    fields.update(overrides)
    return DatasetManifest(**fields)  # type: ignore[arg-type]


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


def _trial(**overrides: object) -> ParameterTrial:
    """Return a valid parameter trial with overridable fields."""
    fields: dict[str, object] = {
        "trial_id": "trial-1",
        "parameters": (Parameter(name="cash_weight", value=0.05),),
        "dataset_fingerprint": "fp-1",
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 3),
        "validation_end": date(2022, 12, 30),
        "seed": 42,
        "code_version": "1.0.0",
        "metric": 0.12,
    }
    fields.update(overrides)
    return ParameterTrial(**fields)  # type: ignore[arg-type]


def _fold(**overrides: object) -> WalkForwardFold:
    """Return a valid walk-forward fold with overridable fields."""
    fields: dict[str, object] = {
        "fold_index": 0,
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 3),
        "validation_end": date(2022, 12, 30),
        "test_start": date(2023, 1, 2),
        "test_end": date(2023, 12, 29),
        "retrain_date": date(2022, 12, 30),
        "dataset_fingerprint": "fp-1",
    }
    fields.update(overrides)
    return WalkForwardFold(**fields)  # type: ignore[arg-type]


class ValidationStatusTests(unittest.TestCase):
    """Verify the validation lifecycle vocabulary (SP 3.13)."""

    def test_statuses_match_the_state_machine(self) -> None:
        self.assertEqual(
            [status.value for status in ValidationStatus],
            [
                "DRAFT",
                "DATA_FROZEN",
                "TUNING",
                "TEST_LOCKED",
                "EVALUATED",
                "NOT_QUALIFIED",
                "FAILED",
            ],
        )

    def test_terminal_statuses_are_distinct(self) -> None:
        self.assertIsNot(ValidationStatus.NOT_QUALIFIED, ValidationStatus.FAILED)
        self.assertIsNot(ValidationStatus.EVALUATED, ValidationStatus.NOT_QUALIFIED)


class OOSConclusionTests(unittest.TestCase):
    """Verify the pre-registered conclusion vocabulary (SP 3.58)."""

    def test_conclusions(self) -> None:
        self.assertEqual(OOSConclusion.QUALIFIED.value, "QUALIFIED")
        self.assertEqual(OOSConclusion.NOT_QUALIFIED.value, "NOT_QUALIFIED")
        self.assertEqual(OOSConclusion.INCONCLUSIVE.value, "INCONCLUSIVE")


class SplitBoundaryErrorTests(unittest.TestCase):
    """Verify the dedicated boundary rejection error (SP 3.4)."""

    def test_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(SplitBoundaryError, ValueError))

    def test_reversed_split_raises_boundary_error(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _split(train_start=date(2022, 1, 1), train_end=date(2019, 1, 1))

    def test_overlapping_split_raises_boundary_error(self) -> None:
        # training ends on the same day validation starts -> must be rejected.
        with self.assertRaises(SplitBoundaryError):
            _split(
                train_end=date(2022, 1, 3),
                validation_start=date(2022, 1, 3),
            )

    def test_test_overlapping_validation_raises(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _split(
                validation_end=date(2023, 1, 2),
                test_start=date(2023, 1, 2),
            )


class DatasetManifestTests(unittest.TestCase):
    """Verify the frozen dataset manifest (SP 3.6 / 3.7)."""

    def test_valid_manifest(self) -> None:
        manifest = _manifest()
        self.assertEqual(manifest.base_currency, Currency.HKD)
        self.assertEqual(manifest.fingerprint, "fp-1")
        self.assertIn("fp-1", manifest.readable())

    def test_requires_a_market(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one market"):
            _manifest(markets=())

    def test_requires_version_fields(self) -> None:
        for field in (
            "config_hash",
            "code_version",
            "calendar_version",
            "fx_source",
            "fingerprint",
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "must be non-empty"):
                    _manifest(**{field: ""})

    def test_rejects_reversed_range(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _manifest(start_date=date(2024, 12, 31), end_date=date(2019, 1, 1))

    def test_rejects_cutoff_outside_range(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _manifest(data_cutoff=date(2018, 12, 31))

    def test_is_frozen(self) -> None:
        manifest = _manifest()
        with self.assertRaises(FrozenInstanceError):
            manifest.fingerprint = "changed"  # type: ignore[misc]


class EvaluationSplitTests(unittest.TestCase):
    """Verify the frozen train / validation / test split (SP 3.4)."""

    def test_valid_split_and_day_counts(self) -> None:
        split = _split()
        self.assertEqual(split.train_days, 1096)  # 2019-01-01..2021-12-31
        self.assertGreater(split.validation_days, 0)
        self.assertGreater(split.test_days, 0)
        self.assertIn("train", split.readable())

    def test_single_day_validation_is_allowed(self) -> None:
        split = _split(validation_start=date(2022, 1, 3), validation_end=date(2022, 1, 3))
        self.assertEqual(split.validation_days, 1)

    def test_empty_range_is_rejected(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _split(train_start=date(2022, 1, 1), train_end=date(2021, 12, 31))

    def test_touching_boundaries_are_rejected(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _split(validation_end=date(2023, 1, 2), test_start=date(2023, 1, 2))

    def test_is_frozen(self) -> None:
        split = _split()
        with self.assertRaises(FrozenInstanceError):
            split.test_end = date(2025, 1, 1)  # type: ignore[misc]


class ParameterTests(unittest.TestCase):
    """Verify the declared parameter value (SP 3.15)."""

    def test_valid_parameter(self) -> None:
        parameter = Parameter(name="cash_weight", value=0.05)
        self.assertEqual(parameter.value, 0.05)

    def test_rejects_empty_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "Parameter name must be non-empty"):
            Parameter(name="", value=1)


class ParameterTrialTests(unittest.TestCase):
    """Verify the recorded parameter trial (SP 3.18)."""

    def test_valid_trial_and_lookup(self) -> None:
        trial = _trial()
        self.assertEqual(trial.parameter("cash_weight"), 0.05)
        self.assertIsNone(trial.parameter("missing"))
        self.assertIn("trial-1", trial.readable())
        self.assertIn("metric 0.1200", trial.readable())

    def test_requires_identity_fields(self) -> None:
        for field in ("trial_id", "dataset_fingerprint", "code_version"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "must be non-empty"):
                    _trial(**{field: ""})

    def test_failed_trial_carries_no_metric(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not carry a metric"):
            _trial(metric=0.1, failed_reason="boom")

    def test_trial_requires_an_outcome(self) -> None:
        with self.assertRaisesRegex(ValueError, "must carry a metric or a failure reason"):
            _trial(metric=None, failed_reason=None)

    def test_failed_trial_is_readable(self) -> None:
        trial = _trial(metric=None, failed_reason="boom")
        self.assertIn("failed: boom", trial.readable())

    def test_rejects_reversed_train_validation(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _trial(train_end=date(2022, 1, 3), validation_start=date(2022, 1, 3))

    def test_is_frozen(self) -> None:
        trial = _trial()
        with self.assertRaises(FrozenInstanceError):
            trial.metric = 0.99  # type: ignore[misc]


class WalkForwardFoldTests(unittest.TestCase):
    """Verify the rolling out-of-sample fold (SP 3.31 / 3.35)."""

    def test_valid_fold_and_readable(self) -> None:
        fold = _fold(run_id="run-1")
        self.assertEqual(fold.fold_index, 0)
        self.assertEqual(fold.run_id, "run-1")
        self.assertIn("fold 0", fold.readable())
        self.assertIn("run run-1", fold.readable())

    def test_rejects_negative_index(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            _fold(fold_index=-1)

    def test_requires_fingerprint(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be non-empty"):
            _fold(dataset_fingerprint="")

    def test_rejects_overlapping_fold_ranges(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _fold(validation_end=date(2023, 1, 2), test_start=date(2023, 1, 2))

    def test_is_frozen(self) -> None:
        fold = _fold()
        with self.assertRaises(FrozenInstanceError):
            fold.run_id = "run-9"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
