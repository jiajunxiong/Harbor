"""Drawdown event analysis tests (MVP 2 / SP 2.56).

Verifies threshold-triggered drawdown interval detection (5% / 8% / 10%), the
capture of the then-held positions (trough valuation) and market / currency /
individual exposure (SP 2.55), and that invalid inputs raise
:class:`DrawdownError` rather than fabricating an interval.
"""

import unittest
from datetime import date, timedelta

from harbor.core.backtest_domain import CashBalance, Currency, Market, NetValue
from harbor.core.drawdown_events import (
    DrawdownConfig,
    DrawdownError,
    DrawdownEvent,
    DrawdownSeries,
    compute_drawdown_events,
)
from harbor.core.exposure import compute_exposure_series
from harbor.core.valuation import DailyValuation, PositionValue

HKD = Currency.HKD
HK = Market.HK

_DAY = date(2024, 1, 2)
_QUANTITY = 200.0


def _day(offset: int) -> date:
    return _DAY + timedelta(days=offset)


def _position_value(price: float) -> PositionValue:
    return PositionValue(
        market=HK,
        symbol="0001.HK",
        quantity=_QUANTITY,
        price=price,
        currency=HKD,
        fx_rate=1.0,
        market_value_quote=_QUANTITY * price,
        market_value_base=_QUANTITY * price,
        carried_forward=False,
        warning=None,
    )


def _valuation(day: date, price: float) -> DailyValuation:
    position = _position_value(price)
    return DailyValuation(
        as_of=day,
        base_currency=HKD,
        cash=(CashBalance(currency=HKD, amount=0.0),),
        position_values=(position,),
        realized_fees=(),
        fx_pnl=0.0,
        net_value=NetValue(
            as_of_date=day,
            currency=HKD,
            cash=0.0,
            securities_value=position.market_value_base,
            fees_paid=0.0,
        ),
    )


def _series(*prices: float) -> tuple[DailyValuation, ...]:
    return tuple(_valuation(_day(offset), price) for offset, price in enumerate(prices))


def _fx(_from: Currency, _to: Currency, _day: date) -> float | None:
    return None


class DrawdownConfigTests(unittest.TestCase):
    """Verify the threshold configuration (SP 2.56)."""

    def test_default_thresholds(self) -> None:
        config = DrawdownConfig()
        self.assertEqual(config.thresholds, (0.05, 0.08, 0.10))

    def test_custom_thresholds(self) -> None:
        config = DrawdownConfig(thresholds=(0.10, 0.20))
        self.assertEqual(config.thresholds, (0.10, 0.20))

    def test_empty_thresholds_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "At least one"):
            DrawdownConfig(thresholds=())

    def test_out_of_range_thresholds_rejected(self) -> None:
        for bad in (0.0, 1.0, -0.1, 1.5):
            with self.assertRaisesRegex(ValueError, "between 0 and 1"):
                DrawdownConfig(thresholds=(bad,))

    def test_not_ascending_rejected(self) -> None:
        for bad in ((0.08, 0.05), (0.05, 0.05)):
            with self.assertRaisesRegex(ValueError, "strictly ascending"):
                DrawdownConfig(thresholds=bad)


class IntervalDetectionTests(unittest.TestCase):
    """Verify the peak-to-trough interval logic (SP 2.56)."""

    def test_no_decline_no_events(self) -> None:
        series = compute_drawdown_events(_series(100.0, 100.0, 100.0))
        self.assertEqual(series.events, ())
        self.assertEqual(series.for_threshold(0.05), ())

    def test_small_dip_only_crosses_lowest_threshold(self) -> None:
        series = compute_drawdown_events(_series(100.0, 94.0, 100.0))
        event = series.for_threshold(0.05)
        self.assertEqual(len(event), 1)
        self.assertEqual(series.for_threshold(0.08), ())
        self.assertEqual(series.for_threshold(0.10), ())
        self.assertEqual(event[0].start_date, _day(1))
        self.assertEqual(event[0].peak_date, _day(0))
        self.assertAlmostEqual(event[0].peak_value, 100.0 * _QUANTITY, places=2)
        self.assertEqual(event[0].trough_date, _day(1))
        self.assertAlmostEqual(event[0].trough_value, 94.0 * _QUANTITY, places=2)
        self.assertAlmostEqual(event[0].depth, 0.06, places=6)
        self.assertEqual(event[0].recovered_date, _day(2))

    def test_deep_dip_triggers_all_thresholds(self) -> None:
        series = compute_drawdown_events(_series(100.0, 85.0, 100.0))
        self.assertEqual(len(series.events), 3)
        for event in series.events:
            self.assertAlmostEqual(event.depth, 0.15, places=6)

    def test_exact_threshold_triggers(self) -> None:
        series = compute_drawdown_events(_series(100.0, 90.0, 100.0))
        event = series.for_threshold(0.10)
        self.assertEqual(len(event), 1)
        self.assertAlmostEqual(event[0].depth, 0.10, places=6)

    def test_continuous_interval_uses_deepest_trough(self) -> None:
        series = compute_drawdown_events(_series(100.0, 95.0, 92.0, 90.0, 100.0))
        event = series.for_threshold(0.05)
        self.assertEqual(len(event), 1)
        self.assertEqual(event[0].start_date, _day(1))
        self.assertEqual(event[0].trough_date, _day(3))
        self.assertAlmostEqual(event[0].depth, 0.10, places=6)
        self.assertEqual(event[0].recovered_date, _day(4))

    def test_recovery_then_new_peak_starts_new_interval(self) -> None:
        series = compute_drawdown_events(_series(100.0, 90.0, 110.0, 100.0))
        event = series.for_threshold(0.05)
        self.assertEqual(len(event), 2)
        self.assertEqual(event[0].recovered_date, _day(2))
        self.assertAlmostEqual(event[1].peak_value, 110.0 * _QUANTITY, places=2)
        self.assertEqual(event[1].recovered_date, None)

    def test_open_interval_no_recovery(self) -> None:
        series = compute_drawdown_events(_series(100.0, 90.0, 92.0))
        event = series.for_threshold(0.08)
        self.assertEqual(len(event), 1)
        self.assertEqual(event[0].trough_date, _day(1))
        self.assertAlmostEqual(event[0].depth, 0.10, places=6)
        self.assertIsNone(event[0].recovered_date)


class MultipleThresholdTests(unittest.TestCase):
    """Verify per-threshold intervals over a multi-dip run (SP 2.56)."""

    def test_two_dips_two_events(self) -> None:
        series = compute_drawdown_events(_series(100.0, 92.0, 100.0, 85.0, 100.0))
        self.assertEqual(len(series.for_threshold(0.05)), 2)
        self.assertEqual(len(series.for_threshold(0.08)), 2)
        ten = series.for_threshold(0.10)
        self.assertEqual(len(ten), 1)
        self.assertEqual(ten[0].start_date, _day(3))
        self.assertAlmostEqual(ten[0].depth, 0.15, places=6)

    def test_for_threshold_returns_empty_for_missing(self) -> None:
        series = compute_drawdown_events(_series(100.0, 92.0, 100.0))
        self.assertEqual(series.for_threshold(0.20), ())


class ExposureCaptureTests(unittest.TestCase):
    """Verify the then-held positions and exposure capture (SP 2.56)."""

    def test_trough_valuation_and_exposure_captured(self) -> None:
        valuations = _series(50.0, 46.0, 50.0)
        exposure = compute_exposure_series(valuations, fx_rate=_fx)
        series = compute_drawdown_events(valuations, exposure=exposure)
        event = series.for_threshold(0.05)[0]
        self.assertIsInstance(event, DrawdownEvent)
        # Then-held positions: the valuation snapshot at the trough.
        self.assertEqual(event.trough_valuation.as_of, _day(1))
        self.assertEqual(len(event.trough_valuation.position_values), 1)
        self.assertAlmostEqual(event.trough_valuation.position_values[0].price, 46.0, places=2)
        # Then-held exposure: market / currency / individual fractions.
        self.assertIsNotNone(event.trough_exposure)
        assert event.trough_exposure is not None
        self.assertEqual(event.trough_exposure.as_of, _day(1))
        self.assertAlmostEqual(event.trough_exposure.market_exposure[HK], 1.0, places=6)
        self.assertAlmostEqual(event.trough_exposure.currency_exposure[HKD], 1.0, places=6)
        self.assertAlmostEqual(
            event.trough_exposure.symbol_exposure[(HK, "0001.HK")], 1.0, places=6
        )

    def test_exposure_none_when_not_provided(self) -> None:
        series = compute_drawdown_events(_series(50.0, 46.0, 50.0))
        event = series.for_threshold(0.05)[0]
        self.assertIsNone(event.trough_exposure)

    def test_exposure_lookup_lenient_when_date_missing(self) -> None:
        valuations = _series(50.0, 46.0, 50.0)
        partial_exposure = compute_exposure_series((valuations[0], valuations[2]), fx_rate=_fx)
        series = compute_drawdown_events(valuations, exposure=partial_exposure)
        event = series.for_threshold(0.05)[0]
        self.assertEqual(event.trough_date, _day(1))
        self.assertIsNone(event.trough_exposure)


class BoundaryTests(unittest.TestCase):
    """Verify refusal on invalid inputs (SP 2.56)."""

    def test_empty_valuations_rejected(self) -> None:
        with self.assertRaisesRegex(DrawdownError, "At least two"):
            compute_drawdown_events(())

    def test_single_valuation_rejected(self) -> None:
        with self.assertRaisesRegex(DrawdownError, "At least two"):
            compute_drawdown_events(_series(100.0))

    def test_nonpositive_total_rejected(self) -> None:
        with self.assertRaisesRegex(DrawdownError, "positive"):
            compute_drawdown_events(_series(100.0, 0.0))

    def test_out_of_order_dates_rejected(self) -> None:
        valuations = (_valuation(_day(1), 100.0), _valuation(_day(0), 100.0))
        with self.assertRaisesRegex(DrawdownError, "ascending"):
            compute_drawdown_events(valuations)

    def test_readable_no_events(self) -> None:
        series = compute_drawdown_events(_series(100.0, 100.0))
        self.assertEqual(series.readable(), "No drawdown events crossed the configured thresholds.")

    def test_readable_with_events_is_research_warning(self) -> None:
        series = compute_drawdown_events(_series(100.0, 90.0, 100.0))
        self.assertIsInstance(series, DrawdownSeries)
        self.assertIn("research-only warning", series.readable())
        self.assertIn("5%", series.readable())


if __name__ == "__main__":
    unittest.main()
