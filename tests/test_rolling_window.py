"""Rolling window generator tests (MVP 3 / SP 3.31).

Verifies the walk-forward generator produces auditable folds from a frozen
pre-registered split: expanding windows (训练随每折增长) and fixed-length
windows (固定长度训练窗口), contiguous non-overlapping out-of-sample segments
that tile the base test interval, and per-fold train / validation / test /
retraining dates that are all recorded and auditable. Retraining dates follow
RetrainFrequency (every fold / quarterly / annual cohorts).
"""

import json
import unittest
from datetime import date, timedelta

from harbor.core.rolling_window import (
    FoldSequence,
    RollingWindowError,
    build_walk_forward_folds,
    folds_fingerprint,
    folds_json,
)
from harbor.core.validation_config import (
    RetrainFrequency,
    RollingWindowConfig,
    RollingWindowMode,
)
from harbor.core.validation_domain import EvaluationSplit, WalkForwardFold

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


def _sequence(**overrides: object) -> FoldSequence:
    """Build a fold sequence with overridable builder arguments."""
    fields: dict[str, object] = {
        "split": _split(),
        "rolling": _rolling(),
        "dataset_fingerprint": _FINGERPRINT,
    }
    fields.update(overrides)
    return build_walk_forward_folds(**fields)  # type: ignore[arg-type]


def _fold(**overrides: object) -> WalkForwardFold:
    """Return a valid fold with overridable fields."""
    fields: dict[str, object] = {
        "fold_index": 0,
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": date(2023, 1, 1),
        "test_end": date(2023, 12, 31),
        "retrain_date": date(2021, 12, 31),
        "dataset_fingerprint": _FINGERPRINT,
    }
    fields.update(overrides)
    return WalkForwardFold(**fields)  # type: ignore[arg-type]


def _quarter(day: date) -> tuple[int, int]:
    """Return the ``(year, quarter_index)`` bucket of a date."""
    return (day.year, (day.month - 1) // 3)


class RollingWindowErrorTests(unittest.TestCase):
    """The dedicated error type and builder guards."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(RollingWindowError, ValueError))

    def test_empty_dataset_fingerprint_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            build_walk_forward_folds(_split(), rolling=_rolling(), dataset_fingerprint="")

    def test_fixed_mode_requires_train_length(self) -> None:
        # RollingWindowConfig (SP 3.2) rejects FIXED without train_length_days at
        # construction; the builder additionally guards defensively.
        with self.assertRaises(ValueError):
            _rolling(mode=RollingWindowMode.FIXED, train_length_days=None)


class ExpandingWindowTests(unittest.TestCase):
    """Expanding window mode: training grows with every fold."""

    def test_fold_zero_reproduces_base_split(self) -> None:
        seq = _sequence()
        fold = seq[0]
        split = _split()
        self.assertEqual(fold.train_start, split.train_start)
        self.assertEqual(fold.train_end, split.train_end)
        self.assertEqual(fold.validation_start, split.validation_start)
        self.assertEqual(fold.validation_end, split.validation_end)
        self.assertEqual(fold.test_start, split.test_start)

    def test_oos_segments_tile_the_horizon(self) -> None:
        seq = _sequence()
        split = _split()
        self.assertEqual(seq.oos_start, split.test_start)
        self.assertEqual(seq.oos_end, split.test_end)
        self.assertEqual(seq[0].test_start, split.test_start)
        self.assertEqual(seq[-1].test_end, split.test_end)

    def test_oos_segments_are_contiguous_and_non_overlapping(self) -> None:
        seq = _sequence()
        for previous, current in zip(seq, seq[1:]):
            self.assertEqual(
                current.test_start,
                previous.test_end + timedelta(days=1),
            )

    def test_fold_count_matches_horizon_and_step(self) -> None:
        seq = _sequence()
        split = _split()
        horizon_days = (split.test_end - split.test_start).days + 1
        expected = (horizon_days - 1) // 365 + 1
        self.assertEqual(len(seq), expected)
        self.assertEqual(seq.folds[-1].fold_index, expected - 1)

    def test_expanding_train_start_is_constant(self) -> None:
        seq = _sequence()
        base = _split().train_start
        for fold in seq:
            self.assertEqual(fold.train_start, base)

    def test_expanding_train_window_grows(self) -> None:
        seq = _sequence()
        for previous, current in zip(seq, seq[1:]):
            self.assertGreater(current.train_end, previous.train_end)
            self.assertGreater(
                (current.train_end - current.train_start).days,
                (previous.train_end - previous.train_start).days,
            )

    def test_train_ends_the_day_before_validation(self) -> None:
        seq = _sequence()
        for fold in seq:
            self.assertEqual(fold.train_end + timedelta(days=1), fold.validation_start)

    def test_validation_length_matches_base_split(self) -> None:
        seq = _sequence()
        expected = _split().validation_days
        for fold in seq:
            self.assertEqual(
                (fold.validation_end - fold.validation_start).days + 1,
                expected,
            )

    def test_validation_ends_the_day_before_test(self) -> None:
        seq = _sequence()
        for fold in seq:
            self.assertEqual(fold.validation_end + timedelta(days=1), fold.test_start)

    def test_single_fold_when_horizon_not_larger_than_step(self) -> None:
        # horizon is 1096 days; a step >= horizon yields exactly one fold.
        seq = _sequence(rolling=_rolling(step_days=1096))
        self.assertEqual(len(seq), 1)
        fold = seq[0]
        split = _split()
        self.assertEqual(fold.test_start, split.test_start)
        self.assertEqual(fold.test_end, split.test_end)
        self.assertEqual(fold.train_start, split.train_start)
        self.assertEqual(fold.train_end, split.train_end)
        self.assertEqual(fold.validation_start, split.validation_start)
        self.assertEqual(fold.validation_end, split.validation_end)

    def test_dataset_fingerprint_propagated_to_every_fold(self) -> None:
        seq = _sequence(dataset_fingerprint="a" * 64)
        for fold in seq:
            self.assertEqual(fold.dataset_fingerprint, "a" * 64)


class FixedWindowTests(unittest.TestCase):
    """Fixed-length window mode: constant train_length_days training window."""

    def _fixed(self, train_length_days: int = 730) -> FoldSequence:
        return _sequence(
            rolling=_rolling(
                mode=RollingWindowMode.FIXED,
                train_length_days=train_length_days,
            )
        )

    def test_every_fold_has_constant_train_length(self) -> None:
        seq = self._fixed(train_length_days=730)
        for fold in seq:
            self.assertEqual(
                (fold.train_end - fold.train_start).days + 1,
                730,
            )

    def test_fixed_train_window_shifts_forward(self) -> None:
        seq = self._fixed(train_length_days=730)
        for previous, current in zip(seq, seq[1:]):
            self.assertEqual(
                current.train_end - current.train_start,
                previous.train_end - previous.train_start,
            )
            self.assertGreater(current.train_start, previous.train_start)
            self.assertGreater(current.train_end, previous.train_end)

    def test_fixed_train_never_overlaps_validation(self) -> None:
        seq = self._fixed(train_length_days=730)
        for fold in seq:
            self.assertEqual(fold.train_end + timedelta(days=1), fold.validation_start)
            self.assertLessEqual(fold.train_start, fold.train_end)

    def test_different_fixed_length_changes_train_windows(self) -> None:
        short = self._fixed(train_length_days=365)
        long = self._fixed(train_length_days=1095)
        self.assertEqual(
            (short[0].train_end - short[0].train_start).days + 1,
            365,
        )
        self.assertEqual(
            (long[0].train_end - long[0].train_start).days + 1,
            1095,
        )
        self.assertNotEqual(short[0].train_start, long[0].train_start)


class RetrainFrequencyTests(unittest.TestCase):
    """Retraining dates are auditable and follow RetrainFrequency."""

    def test_every_fold_retrains_at_its_own_train_end(self) -> None:
        seq = _sequence()
        for fold in seq:
            self.assertEqual(fold.retrain_date, fold.train_end)
        self.assertEqual(len({fold.retrain_date for fold in seq}), len(seq))

    def test_fold_zero_always_retrains(self) -> None:
        for frequency in (
            RetrainFrequency.EVERY_FOLD,
            RetrainFrequency.QUARTERLY,
            RetrainFrequency.ANNUAL,
        ):
            with self.subTest(frequency=frequency):
                seq = _sequence(rolling=_rolling(retrain_frequency=frequency))
                self.assertEqual(seq[0].retrain_date, seq[0].train_end)

    def test_retrain_dates_non_decreasing(self) -> None:
        seq = _sequence()
        for previous, current in zip(seq, seq[1:]):
            self.assertGreaterEqual(current.retrain_date, previous.retrain_date)  # type: ignore[arg-type]

    def test_quarterly_retrains_on_quarter_boundaries(self) -> None:
        split = _split(
            train_start=date(2019, 1, 1),
            train_end=date(2022, 5, 31),
            validation_start=date(2022, 6, 1),
            validation_end=date(2023, 5, 31),
            test_start=date(2023, 6, 1),
            test_end=date(2024, 5, 31),
        )
        seq = _sequence(
            split=split,
            rolling=_rolling(
                step_days=30,
                retrain_frequency=RetrainFrequency.QUARTERLY,
            ),
        )
        self.assertGreater(len(seq), 4)
        for previous, current in zip(seq, seq[1:]):
            same_quarter = _quarter(previous.train_end) == _quarter(current.train_end)
            if same_quarter:
                self.assertEqual(current.retrain_date, previous.retrain_date)
            else:
                self.assertEqual(current.retrain_date, current.train_end)

    def test_annual_retrains_on_year_boundaries(self) -> None:
        split = _split(
            train_start=date(2019, 1, 1),
            train_end=date(2022, 5, 31),
            validation_start=date(2022, 6, 1),
            validation_end=date(2023, 5, 31),
            test_start=date(2023, 6, 1),
            test_end=date(2026, 5, 31),
        )
        seq = _sequence(
            split=split,
            rolling=_rolling(
                step_days=30,
                retrain_frequency=RetrainFrequency.ANNUAL,
            ),
        )
        for previous, current in zip(seq, seq[1:]):
            if previous.train_end.year == current.train_end.year:
                self.assertEqual(current.retrain_date, previous.retrain_date)
            else:
                self.assertEqual(current.retrain_date, current.train_end)

    def test_retrain_date_within_training_window(self) -> None:
        seq = _sequence()
        for fold in seq:
            self.assertLessEqual(fold.retrain_date, fold.train_end)  # type: ignore[arg-type]
            self.assertGreaterEqual(fold.retrain_date, fold.train_start)  # type: ignore[arg-type]


class FoldAuditabilityTests(unittest.TestCase):
    """Every fold records auditable train / validation / test / retrain dates."""

    def test_every_fold_carries_all_dates(self) -> None:
        seq = _sequence()
        for fold in seq:
            self.assertIsNotNone(fold.train_start)
            self.assertIsNotNone(fold.train_end)
            self.assertIsNotNone(fold.validation_start)
            self.assertIsNotNone(fold.validation_end)
            self.assertIsNotNone(fold.test_start)
            self.assertIsNotNone(fold.test_end)
            self.assertIsNotNone(fold.retrain_date)

    def test_every_fold_is_readable(self) -> None:
        seq = _sequence()
        for fold in seq:
            line = fold.readable()
            self.assertIn("fold", line)
            self.assertIn("train", line)
            self.assertIn("validation", line)
            self.assertIn("test", line)
            self.assertIn("retrain", line)

    def test_sequence_readable(self) -> None:
        seq = _sequence()
        line = seq.readable()
        self.assertIn(f"{len(seq)} folds", line)
        self.assertIn("OOS", line)

    def test_iteration_and_indexing(self) -> None:
        seq = _sequence()
        self.assertEqual(len(seq), len(list(seq)))
        self.assertEqual(list(seq)[2].fold_index, seq[2].fold_index)
        self.assertEqual(seq[0].fold_index, 0)
        with self.assertRaises(IndexError):
            seq[len(seq)]


class FoldSequenceValidationTests(unittest.TestCase):
    """FoldSequence rejects an inconsistent, un-auditable sequence."""

    def _sequence_of(self, folds: tuple[WalkForwardFold, ...]) -> FoldSequence:
        return FoldSequence(folds=folds, fingerprint="abc")

    def test_empty_sequence_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            self._sequence_of(())

    def test_non_sequential_indices_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            self._sequence_of(
                (
                    _fold(fold_index=0),
                    _fold(
                        fold_index=1,
                        train_start=date(2019, 1, 1),
                        train_end=date(2022, 12, 31),
                        validation_start=date(2023, 1, 1),
                        validation_end=date(2023, 12, 31),
                        test_start=date(2024, 1, 1),
                        test_end=date(2024, 12, 31),
                    ),
                    _fold(
                        fold_index=3,
                        train_start=date(2019, 1, 1),
                        train_end=date(2023, 12, 31),
                        validation_start=date(2024, 1, 1),
                        validation_end=date(2024, 12, 31),
                        test_start=date(2025, 1, 1),
                        test_end=date(2025, 12, 31),
                    ),
                )
            )

    def test_overlapping_oos_segments_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            self._sequence_of(
                (
                    _fold(fold_index=0),
                    _fold(
                        fold_index=1,
                        train_start=date(2019, 1, 1),
                        train_end=date(2022, 12, 31),
                        validation_start=date(2023, 1, 1),
                        validation_end=date(2023, 5, 31),
                        test_start=date(2023, 6, 1),
                        test_end=date(2024, 12, 31),
                    ),
                )
            )

    def test_gapped_oos_segments_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            self._sequence_of(
                (
                    _fold(fold_index=0),
                    _fold(
                        fold_index=1,
                        train_start=date(2019, 1, 1),
                        train_end=date(2022, 12, 31),
                        validation_start=date(2023, 1, 1),
                        validation_end=date(2023, 12, 31),
                        test_start=date(2024, 2, 1),
                        test_end=date(2024, 12, 31),
                    ),
                )
            )

    def test_mixed_dataset_fingerprints_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            self._sequence_of(
                (
                    _fold(fold_index=0),
                    _fold(
                        fold_index=1,
                        dataset_fingerprint="g" * 64,
                        train_start=date(2019, 1, 1),
                        train_end=date(2022, 12, 31),
                        validation_start=date(2023, 1, 1),
                        validation_end=date(2023, 12, 31),
                        test_start=date(2024, 1, 1),
                        test_end=date(2024, 12, 31),
                    ),
                )
            )

    def test_missing_retrain_date_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            self._sequence_of(
                (
                    _fold(fold_index=0),
                    _fold(
                        fold_index=1,
                        retrain_date=None,
                        train_start=date(2019, 1, 1),
                        train_end=date(2022, 12, 31),
                        validation_start=date(2023, 1, 1),
                        validation_end=date(2023, 12, 31),
                        test_start=date(2024, 1, 1),
                        test_end=date(2024, 12, 31),
                    ),
                )
            )

    def test_decreasing_retrain_dates_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            self._sequence_of(
                (
                    _fold(fold_index=0),
                    _fold(
                        fold_index=1,
                        retrain_date=date(2021, 6, 30),
                        train_start=date(2019, 1, 1),
                        train_end=date(2022, 12, 31),
                        validation_start=date(2023, 1, 1),
                        validation_end=date(2023, 12, 31),
                        test_start=date(2024, 1, 1),
                        test_end=date(2024, 12, 31),
                    ),
                )
            )

    def test_retrain_after_training_end_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            self._sequence_of(
                (
                    _fold(fold_index=0),
                    _fold(
                        fold_index=1,
                        retrain_date=date(2025, 1, 1),
                        train_start=date(2019, 1, 1),
                        train_end=date(2022, 12, 31),
                        validation_start=date(2023, 1, 1),
                        validation_end=date(2023, 12, 31),
                        test_start=date(2024, 1, 1),
                        test_end=date(2024, 12, 31),
                    ),
                )
            )

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            FoldSequence(folds=(_fold(),), fingerprint="")

    def test_fingerprint_must_be_non_empty(self) -> None:
        seq = _sequence()
        self.assertTrue(seq.fingerprint)


class FingerprintTests(unittest.TestCase):
    """The fold-sequence fingerprint is stable and re-derivable."""

    def test_fingerprint_is_sha256_hex(self) -> None:
        seq = _sequence()
        self.assertRegex(seq.fingerprint, r"^[0-9a-f]{64}$")

    def test_fingerprint_stable_across_equal_builds(self) -> None:
        self.assertEqual(_sequence().fingerprint, _sequence().fingerprint)

    def test_fingerprint_rederivable(self) -> None:
        seq = _sequence()
        self.assertEqual(seq.fingerprint, folds_fingerprint(seq))

    def test_fingerprint_changes_with_mode(self) -> None:
        expanding = _sequence().fingerprint
        fixed = _sequence(
            rolling=_rolling(mode=RollingWindowMode.FIXED, train_length_days=730)
        ).fingerprint
        self.assertNotEqual(expanding, fixed)

    def test_fingerprint_changes_with_step(self) -> None:
        self.assertNotEqual(
            _sequence(rolling=_rolling(step_days=180)).fingerprint,
            _sequence(rolling=_rolling(step_days=365)).fingerprint,
        )

    def test_fingerprint_changes_with_retrain_frequency(self) -> None:
        # step 365 crosses a new quarter every fold (identical to EVERY_FOLD), so
        # use a small step where folds share quarters to observe the difference.
        split = _split(
            train_start=date(2019, 1, 1),
            train_end=date(2022, 5, 31),
            validation_start=date(2022, 6, 1),
            validation_end=date(2023, 5, 31),
            test_start=date(2023, 6, 1),
            test_end=date(2024, 5, 31),
        )
        every_fold = _sequence(split=split, rolling=_rolling(step_days=30)).fingerprint
        quarterly = _sequence(
            split=split,
            rolling=_rolling(step_days=30, retrain_frequency=RetrainFrequency.QUARTERLY),
        ).fingerprint
        self.assertNotEqual(every_fold, quarterly)

    def test_fingerprint_changes_with_dataset_fingerprint(self) -> None:
        self.assertNotEqual(
            _sequence(dataset_fingerprint="a" * 64).fingerprint,
            _sequence().fingerprint,
        )

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(folds_json(_sequence()))
        self.assertNotIn("fingerprint", payload)
        self.assertIn("folds", payload)
        self.assertEqual(len(payload["folds"]), len(_sequence()))

    def test_json_is_stable_and_key_sorted(self) -> None:
        self.assertEqual(folds_json(_sequence()), folds_json(_sequence()))
        payload = json.loads(folds_json(_sequence()))
        self.assertEqual(list(payload.keys()), ["folds"])
        first = payload["folds"][0]
        self.assertEqual(
            list(first.keys()),
            [
                "dataset_fingerprint",
                "fold_index",
                "retrain_date",
                "run_id",
                "test_end",
                "test_start",
                "train_end",
                "train_start",
                "validation_end",
                "validation_start",
            ],
        )

    def test_json_changes_with_step(self) -> None:
        self.assertNotEqual(
            folds_json(_sequence(rolling=_rolling(step_days=180))),
            folds_json(_sequence()),
        )


if __name__ == "__main__":
    unittest.main()
