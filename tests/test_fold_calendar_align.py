"""Fold boundary and calendar alignment tests (MVP 3 / SP 3.32).

Verifies fold boundaries from SP 3.31 are aligned to tradable days using the
MVP 2 HK/US authoritative trading calendar: start boundaries align forward,
end boundaries and the retraining date align backward, and a cross-market
validation records every market's actual aligned dates. A window that is
entirely non-trading is rejected rather than silently adjusted.
"""

import json
import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.fold_calendar_align import (
    CalendarAlignedFold,
    CalendarAlignedSequence,
    CalendarAlignmentError,
    MarketAlignedDates,
    align_fold_boundaries,
    aligned_fingerprint,
    aligned_json,
)
from harbor.core.rolling_window import FoldSequence, build_walk_forward_folds
from harbor.core.trading_calendar import MarketTradingCalendar
from harbor.core.validation_config import (
    RetrainFrequency,
    RollingWindowConfig,
    RollingWindowMode,
)
from harbor.core.validation_domain import EvaluationSplit, WalkForwardFold

_FINGERPRINT = "f" * 64


def _split(**overrides: object) -> EvaluationSplit:
    """Return a tight split whose boundaries land on weekends (overridable)."""
    fields: dict[str, object] = {
        "train_start": date(2022, 1, 1),
        "train_end": date(2022, 12, 31),
        "validation_start": date(2023, 1, 1),
        "validation_end": date(2023, 12, 30),
        "test_start": date(2023, 12, 31),
        "test_end": date(2026, 12, 31),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _rolling(**overrides: object) -> RollingWindowConfig:
    """Return an expanding, every-fold rolling config (overridable)."""
    fields: dict[str, object] = {
        "mode": RollingWindowMode.EXPANDING,
        "train_length_days": None,
        "step_days": 365,
        "retrain_frequency": RetrainFrequency.EVERY_FOLD,
    }
    fields.update(overrides)
    return RollingWindowConfig(**fields)  # type: ignore[arg-type]


def _sequence(**overrides: object) -> FoldSequence:
    """Build the SP 3.31 fold sequence with overridable builder arguments."""
    fields: dict[str, object] = {
        "split": _split(),
        "rolling": _rolling(),
        "dataset_fingerprint": _FINGERPRINT,
    }
    fields.update(overrides)
    return build_walk_forward_folds(**fields)  # type: ignore[arg-type]


def _calendar() -> MarketTradingCalendar:
    """A calendar where HK observes a Monday holiday US does not (2023-01-02)."""
    return MarketTradingCalendar(
        holidays={
            Market.HK: frozenset({date(2023, 1, 2)}),
            Market.US: frozenset(),
        }
    )


def _aligned(**overrides: object) -> CalendarAlignedSequence:
    """Align the default sequence with overridable arguments."""
    fields: dict[str, object] = {
        "sequence": _sequence(),
        "markets": (Market.HK, Market.US),
        "calendar": _calendar(),
        "calendar_version": "test-2023",
    }
    fields.update(overrides)
    return align_fold_boundaries(**fields)  # type: ignore[arg-type]


def _aligned_dates(**overrides: object) -> MarketAlignedDates:
    """Return aligned US dates with overridable fields."""
    fields: dict[str, object] = {
        "market": Market.US,
        "train_start": date(2022, 1, 3),
        "train_end": date(2022, 12, 30),
        "validation_start": date(2023, 1, 2),
        "validation_end": date(2023, 12, 29),
        "test_start": date(2024, 1, 1),
        "test_end": date(2024, 12, 27),
        "retrain_date": date(2022, 12, 30),
    }
    fields.update(overrides)
    return MarketAlignedDates(**fields)  # type: ignore[arg-type]


def _raw_fold(**overrides: object) -> WalkForwardFold:
    """Return a raw SP 3.31 fold with overridable fields."""
    fields: dict[str, object] = {
        "fold_index": 0,
        "train_start": date(2022, 1, 1),
        "train_end": date(2022, 12, 31),
        "validation_start": date(2023, 1, 1),
        "validation_end": date(2023, 12, 30),
        "test_start": date(2023, 12, 31),
        "test_end": date(2024, 12, 29),
        "retrain_date": date(2022, 12, 31),
        "dataset_fingerprint": _FINGERPRINT,
    }
    fields.update(overrides)
    return WalkForwardFold(**fields)  # type: ignore[arg-type]


class CalendarAlignmentErrorTests(unittest.TestCase):
    """The dedicated error type and builder guards."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(CalendarAlignmentError, ValueError))

    def test_empty_markets_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            _aligned(markets=())

    def test_duplicate_markets_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            _aligned(markets=(Market.US, Market.US))


class AlignmentRuleTests(unittest.TestCase):
    """Start-forward / end-backward alignment to tradable days."""

    def test_start_boundaries_align_forward(self) -> None:
        us = _aligned()[0].dates_for(Market.US)
        self.assertIsNotNone(us)
        assert us is not None
        # raw train_start was a Saturday -> first trading day is Monday.
        self.assertEqual(us.train_start, date(2022, 1, 3))
        # raw validation_start was a Sunday -> Monday.
        self.assertEqual(us.validation_start, date(2023, 1, 2))
        # raw test_start was a Sunday -> Monday 2024-01-01.
        self.assertEqual(us.test_start, date(2024, 1, 1))

    def test_end_boundaries_align_backward(self) -> None:
        us = _aligned()[0].dates_for(Market.US)
        assert us is not None
        # raw train_end was a Saturday -> Friday.
        self.assertEqual(us.train_end, date(2022, 12, 30))
        # raw validation_end was a Saturday -> Friday.
        self.assertEqual(us.validation_end, date(2023, 12, 29))
        # raw test_end 2024-12-29 was a Sunday -> Friday.
        self.assertEqual(us.test_end, date(2024, 12, 27))

    def test_retrain_date_aligns_backward(self) -> None:
        us = _aligned()[0].dates_for(Market.US)
        assert us is not None
        # raw retrain_date 2022-12-31 was a Saturday -> Friday.
        self.assertEqual(us.retrain_date, date(2022, 12, 30))

    def test_fold_zero_full_us_alignment(self) -> None:
        us = _aligned()[0].dates_for(Market.US)
        assert us is not None
        self.assertEqual(
            us.readable(),
            "US train 2022-01-03..2022-12-30 validation 2023-01-02..2023-12-29 "
            "test 2024-01-01..2024-12-27 retrain 2022-12-30",
        )

    def test_boundary_already_trading_is_unchanged(self) -> None:
        # A boundary on a weekday (trading) is its own alignment.
        us = _aligned()[0].dates_for(Market.US)
        assert us is not None
        self.assertEqual(us.test_start, date(2024, 1, 1))
        self.assertTrue(us.test_start.weekday() < 5)


class CrossMarketTests(unittest.TestCase):
    """Cross-market folds record every market's actual aligned dates."""

    def test_every_fold_carries_both_markets(self) -> None:
        aligned = _aligned()
        self.assertEqual(aligned.markets, (Market.HK, Market.US))
        for fold in aligned:
            self.assertEqual([m.market for m in fold.markets], [Market.HK, Market.US])

    def test_hk_holiday_shifts_only_hk_validation_start(self) -> None:
        fold = _aligned()[0]
        hk = fold.dates_for(Market.HK)
        us = fold.dates_for(Market.US)
        assert hk is not None and us is not None
        # 2023-01-02 is a Monday holiday for HK only.
        self.assertEqual(hk.validation_start, date(2023, 1, 3))
        self.assertEqual(us.validation_start, date(2023, 1, 2))
        # All other boundaries agree between the two markets.
        self.assertEqual(hk.train_start, us.train_start)
        self.assertEqual(hk.train_end, us.train_end)
        self.assertEqual(hk.validation_end, us.validation_end)
        self.assertEqual(hk.test_start, us.test_start)
        self.assertEqual(hk.test_end, us.test_end)

    def test_dates_for_returns_none_for_absent_market(self) -> None:
        # A US-only alignment has no HK dates to report.
        us_only = _aligned(markets=(Market.US,))[0]
        self.assertIsNone(us_only.dates_for(Market.HK))
        self.assertIsNotNone(us_only.dates_for(Market.US))


class OrderingPreservationTests(unittest.TestCase):
    """Aligned boundaries stay non-empty and strictly ordered, else rejected."""

    def test_aligned_fold_preserves_ordering(self) -> None:
        for fold in _aligned():
            for aligned in fold.markets:
                self.assertLessEqual(aligned.train_start, aligned.train_end)
                self.assertLess(aligned.train_end, aligned.validation_start)
                self.assertLessEqual(aligned.validation_start, aligned.validation_end)
                self.assertLess(aligned.validation_end, aligned.test_start)
                self.assertLessEqual(aligned.test_start, aligned.test_end)
                self.assertLessEqual(aligned.retrain_date, aligned.train_end)  # type: ignore[arg-type]

    def test_entirely_non_trading_test_window_rejected(self) -> None:
        fold = _raw_fold(
            validation_start=date(2023, 1, 2),
            validation_end=date(2023, 1, 6),
            test_start=date(2023, 1, 7),  # Saturday
            test_end=date(2023, 1, 8),  # Sunday
        )
        sequence = FoldSequence(folds=(fold,), fingerprint="abc")
        with self.assertRaises(CalendarAlignmentError) as ctx:
            align_fold_boundaries(
                sequence,
                markets=(Market.US,),
                calendar=_calendar(),
                calendar_version="test",
            )
        self.assertIn("empty or reversed", str(ctx.exception))

    def test_entirely_non_trading_validation_window_rejected(self) -> None:
        fold = _raw_fold(
            validation_start=date(2023, 1, 7),  # Saturday
            validation_end=date(2023, 1, 8),  # Sunday
            test_start=date(2023, 1, 9),
            test_end=date(2023, 1, 13),
        )
        sequence = FoldSequence(folds=(fold,), fingerprint="abc")
        with self.assertRaises(CalendarAlignmentError):
            align_fold_boundaries(
                sequence,
                markets=(Market.US,),
                calendar=_calendar(),
                calendar_version="test",
            )


class MarketAlignedDatesTests(unittest.TestCase):
    """The per-market aligned value validates its own ranges."""

    def test_reversed_train_range_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            _aligned_dates(train_start=date(2022, 12, 30), train_end=date(2022, 1, 3))

    def test_overlapping_train_validation_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            _aligned_dates(validation_start=date(2022, 12, 30))

    def test_retrain_after_train_end_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            _aligned_dates(retrain_date=date(2023, 1, 3))

    def test_frozen(self) -> None:
        aligned = _aligned_dates()
        with self.assertRaises(Exception):
            aligned.test_start = date(2024, 1, 2)  # type: ignore[misc]


class CalendarAlignedFoldTests(unittest.TestCase):
    """The per-fold aligned record validates markets and is auditable."""

    def test_empty_markets_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            CalendarAlignedFold(fold=_raw_fold(), markets=())

    def test_unsorted_markets_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            CalendarAlignedFold(
                fold=_raw_fold(),
                markets=(_aligned_dates(), _aligned_dates(market=Market.HK)),
            )

    def test_duplicate_markets_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            CalendarAlignedFold(
                fold=_raw_fold(),
                markets=(_aligned_dates(), _aligned_dates()),
            )

    def test_dates_for_lookup(self) -> None:
        fold = CalendarAlignedFold(
            fold=_raw_fold(),
            markets=(_aligned_dates(market=Market.HK), _aligned_dates()),
        )
        self.assertIsNotNone(fold.dates_for(Market.HK))
        self.assertIsNotNone(fold.dates_for(Market.US))

    def test_readable(self) -> None:
        fold = CalendarAlignedFold(
            fold=_raw_fold(),
            markets=(_aligned_dates(market=Market.HK), _aligned_dates()),
        )
        self.assertIn("fold 0", fold.readable())
        self.assertIn("HK", fold.readable())
        self.assertIn("US", fold.readable())


class CalendarAlignedSequenceTests(unittest.TestCase):
    """The aligned sequence validates consistency and is iterable."""

    def _fold(self, **overrides: object) -> CalendarAlignedFold:
        fields: dict[str, object] = {
            "fold": _raw_fold(),
            "markets": (_aligned_dates(market=Market.HK), _aligned_dates()),
        }
        fields.update(overrides)
        return CalendarAlignedFold(**fields)  # type: ignore[arg-type]

    def _sequence(self, *folds: CalendarAlignedFold) -> CalendarAlignedSequence:
        return CalendarAlignedSequence(
            folds=folds,
            calendar_version="test-2023",
            fingerprint="abc",
        )

    def test_empty_folds_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            self._sequence()

    def test_empty_calendar_version_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            CalendarAlignedSequence(
                folds=(self._fold(),),
                calendar_version="",
                fingerprint="abc",
            )

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            CalendarAlignedSequence(
                folds=(self._fold(),),
                calendar_version="test",
                fingerprint="",
            )

    def test_non_sequential_indices_rejected(self) -> None:
        second = self._fold(fold=_raw_fold(fold_index=2))
        with self.assertRaises(CalendarAlignmentError):
            self._sequence(self._fold(fold=_raw_fold(fold_index=0)), second)

    def test_inconsistent_markets_across_folds_rejected(self) -> None:
        hk_only = CalendarAlignedFold(
            fold=_raw_fold(),
            markets=(_aligned_dates(market=Market.HK),),
        )
        with self.assertRaises(CalendarAlignmentError):
            self._sequence(self._fold(), hk_only)

    def test_len_iter_getitem(self) -> None:
        seq = self._sequence(
            self._fold(fold=_raw_fold(fold_index=0)),
            self._fold(fold=_raw_fold(fold_index=1)),
        )
        self.assertEqual(len(seq), 2)
        self.assertEqual(list(seq)[1].fold.fold_index, seq[1].fold.fold_index)
        with self.assertRaises(IndexError):
            seq[2]

    def test_markets_property_and_readable(self) -> None:
        seq = self._sequence(self._fold())
        self.assertEqual(seq.markets, (Market.HK, Market.US))
        line = seq.readable()
        self.assertIn("1 aligned folds", line)
        self.assertIn("HK, US", line)
        self.assertIn("test-2023", line)


class AlignmentFingerprintTests(unittest.TestCase):
    """The aligned-sequence fingerprint is stable and re-derivable."""

    def test_fingerprint_is_sha256_hex(self) -> None:
        self.assertRegex(_aligned().fingerprint, r"^[0-9a-f]{64}$")

    def test_fingerprint_rederivable(self) -> None:
        aligned = _aligned()
        self.assertEqual(aligned.fingerprint, aligned_fingerprint(aligned))

    def test_fingerprint_stable_across_equal_builds(self) -> None:
        self.assertEqual(_aligned().fingerprint, _aligned().fingerprint)

    def test_fingerprint_changes_with_calendar_version(self) -> None:
        self.assertNotEqual(
            _aligned(calendar_version="v1").fingerprint,
            _aligned(calendar_version="v2").fingerprint,
        )

    def test_fingerprint_changes_with_markets(self) -> None:
        self.assertNotEqual(
            _aligned(markets=(Market.US,)).fingerprint,
            _aligned(markets=(Market.HK, Market.US)).fingerprint,
        )

    def test_fingerprint_changes_with_holidays(self) -> None:
        no_holiday = MarketTradingCalendar(
            holidays={Market.HK: frozenset(), Market.US: frozenset()}
        )
        self.assertNotEqual(
            _aligned(calendar=no_holiday).fingerprint,
            _aligned().fingerprint,
        )

    def test_fingerprint_changes_with_fold_geometry(self) -> None:
        self.assertNotEqual(
            _aligned(sequence=_sequence(rolling=_rolling(step_days=180))).fingerprint,
            _aligned().fingerprint,
        )

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(aligned_json(_aligned()))
        self.assertNotIn("fingerprint", payload)
        self.assertIn("calendar_version", payload)
        self.assertIn("folds", payload)
        self.assertEqual(len(payload["folds"]), len(_aligned()))

    def test_json_is_key_sorted(self) -> None:
        payload = json.loads(aligned_json(_aligned()))
        self.assertEqual(list(payload.keys()), ["calendar_version", "folds"])
        markets = payload["folds"][0]["markets"]
        self.assertEqual([m["market"] for m in markets], ["HK", "US"])
        self.assertEqual(
            list(markets[0].keys()),
            [
                "market",
                "retrain_date",
                "test_end",
                "test_start",
                "train_end",
                "train_start",
                "validation_end",
                "validation_start",
            ],
        )

    def test_json_records_each_market_actual_dates(self) -> None:
        payload = json.loads(aligned_json(_aligned()))
        first = payload["folds"][0]["markets"][0]
        self.assertEqual(first["market"], "HK")
        self.assertEqual(first["validation_start"], "2023-01-03")


if __name__ == "__main__":
    unittest.main()
