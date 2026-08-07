"""Consolidated HK/US cost and calendar unit tests (MVP 2 / SP 2.49).

Separately verifies each acceptance dimension of the combined suite:
- holiday deferral (节假日顺延) via the rebalance schedule (SP 2.33) and the
  market trading calendar (SP 2.11);
- trading costs (成本) via the HK (SP 2.37) and US (SP 2.38) cost models;
- board lots / fractional shares (手数/碎股) rounding rules;
- minimum fees (最小收费) from the configuration;
- FX conversion (汇率换算) in the multi-currency ledger (SP 2.42);
- suspension / untradeable handling (停牌) (SP 2.41).

Each area is verified independently, never assuming a unified fee or calendar
across the two markets (MVP 2 acceptance criteria).
"""

import unittest
from datetime import date

from harbor.core.backtest_config import CostConfig, SuspensionConfig
from harbor.core.backtest_domain import Currency, Market, Order, OrderSide
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.cost_hk import HkOrderCost, hk_order_cost, round_to_lot
from harbor.core.cost_us import UsOrderCost, round_to_fraction, us_order_cost
from harbor.core.fx import FxConversionError
from harbor.core.ledger import (
    InsufficientCashError,
    convert,
    credit,
    deposit,
    empty_ledger,
)
from harbor.core.rebalance_schedule import (
    DeferralRule,
    RebalanceAnchor,
    RebalanceSchedule,
    generate_rebalance_days,
)
from harbor.core.suspension import (
    PositionValuation,
    RefusedOrder,
    SuspensionWarning,
    is_tradeable,
    position_valuation_price,
    refuse_order,
)
from harbor.core.trading_calendar import MarketTradingCalendar

HKD = Currency.HKD
USD = Currency.USD
_BUY = OrderSide.BUY
_SELL = OrderSide.SELL

_DAY = date(2024, 1, 2)
_LAST = date(2023, 12, 29)


def _calendar(
    hk_holidays: frozenset[date] = frozenset(),
    us_holidays: frozenset[date] = frozenset(),
) -> MarketTradingCalendar:
    """Return a calendar with the given market holidays (weekdays only)."""
    return MarketTradingCalendar({Market.HK: hk_holidays, Market.US: us_holidays})


def _quote(
    *,
    day: date = _DAY,
    close: float = 11.0,
    volume: int = 1_000,
    market: Market = Market.HK,
    symbol: str = "0001.HK",
) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _order(
    *,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    side: OrderSide = _BUY,
    quantity: float = 800.0,
    currency: Currency = HKD,
) -> Order:
    return Order(
        symbol=symbol,
        market=market,
        side=side,
        quantity=quantity,
        currency=currency,
        trade_date=_DAY,
        ref="r1",
    )


def _ledger(*, base: Currency = HKD, as_of: date = _DAY):
    return empty_ledger(as_of=as_of, base_currency=base)


class HolidayDeferralTests(unittest.TestCase):
    """Verify holiday deferral and per-market calendars (SP 2.33 / 2.11)."""

    def test_quarter_start_forward_deferral_on_holiday(self) -> None:
        calendar = _calendar(hk_holidays=frozenset({date(2026, 4, 1)}))
        days = generate_rebalance_days(
            Market.HK,
            date(2026, 4, 1),
            date(2026, 4, 30),
            calendar,
            RebalanceSchedule(anchor=RebalanceAnchor.QUARTER_START),
        )
        self.assertEqual(days, (date(2026, 4, 2),))

    def test_backward_deferral_uses_previous_trading_day(self) -> None:
        # Jul 4 2026 is a Saturday; backward defers to Friday Jul 3.
        days = generate_rebalance_days(
            Market.HK,
            date(2026, 7, 1),
            date(2026, 7, 31),
            _calendar(),
            RebalanceSchedule(
                anchor=RebalanceAnchor.SPECIFIED,
                specified_dates=(date(2026, 7, 4),),
                deferral=DeferralRule.BACKWARD,
            ),
        )
        self.assertEqual(days, (date(2026, 7, 3),))

    def test_hk_and_us_defer_independently(self) -> None:
        # Apr 1 2026 is a Wednesday; a HK-only holiday must not move the US day.
        calendar = _calendar(hk_holidays=frozenset({date(2026, 4, 1)}))
        schedule = RebalanceSchedule(anchor=RebalanceAnchor.QUARTER_START)
        hk = generate_rebalance_days(
            Market.HK, date(2026, 4, 1), date(2026, 4, 30), calendar, schedule
        )
        us = generate_rebalance_days(
            Market.US, date(2026, 4, 1), date(2026, 4, 30), calendar, schedule
        )
        self.assertEqual(hk, (date(2026, 4, 2),))
        self.assertEqual(us, (date(2026, 4, 1),))

    def test_deferred_day_outside_range_is_dropped(self) -> None:
        # Jan 2 2026 (Fri) is a HK holiday; forward deferral lands on Mon Jan 5,
        # outside the requested end date, so no rebalance day is produced.
        calendar = _calendar(hk_holidays=frozenset({date(2026, 1, 2)}))
        days = generate_rebalance_days(
            Market.HK,
            date(2026, 1, 1),
            date(2026, 1, 2),
            calendar,
            RebalanceSchedule(
                anchor=RebalanceAnchor.SPECIFIED,
                specified_dates=(date(2026, 1, 2),),
            ),
        )
        self.assertEqual(days, ())

    def test_calendar_next_and_previous_trading_day(self) -> None:
        calendar = _calendar(hk_holidays=frozenset({date(2026, 1, 2)}))
        # Jan 2 2026 is a Friday holiday: next is Mon Jan 5, previous is Thu Jan 1.
        self.assertEqual(calendar.next_trading_day(Market.HK, date(2026, 1, 2)), date(2026, 1, 5))
        self.assertEqual(
            calendar.previous_trading_day(Market.HK, date(2026, 1, 2)), date(2026, 1, 1)
        )

    def test_weekend_is_not_a_trading_day(self) -> None:
        self.assertFalse(_calendar().is_trading_day(Market.HK, date(2026, 7, 4)))
        self.assertFalse(_calendar().is_trading_day(Market.US, date(2026, 7, 4)))


class HkCostTests(unittest.TestCase):
    """Verify the Hong Kong cost model (SP 2.37)."""

    def test_commission_is_max_of_rate_and_min(self) -> None:
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=10_000, price=100.0)
        self.assertIsInstance(cost, HkOrderCost)
        self.assertEqual(cost.notional, 1_000_000.0)
        self.assertEqual(cost.commission, 500.0)  # 0.0005 * 1,000,000

    def test_proportional_fees(self) -> None:
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=10_000, price=100.0)
        self.assertAlmostEqual(cost.stamp_duty, 1_000.0, places=2)
        self.assertAlmostEqual(cost.transaction_levy, 27.0, places=2)
        self.assertAlmostEqual(cost.trading_fee, 56.5, places=2)
        self.assertAlmostEqual(cost.total_fee, 1_583.5, places=2)

    def test_buy_and_sell_incur_same_hk_fees(self) -> None:
        buy = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=10_000, price=100.0)
        sell = hk_order_cost(symbol="0001.HK", side=_SELL, quantity=10_000, price=100.0)
        self.assertEqual(buy.commission, sell.commission)
        self.assertEqual(buy.total_fee, sell.total_fee)

    def test_fees_are_rounded_to_cents(self) -> None:
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=3, price=0.333)
        for value in (
            cost.commission,
            cost.stamp_duty,
            cost.transaction_levy,
            cost.trading_fee,
            cost.total_fee,
        ):
            self.assertAlmostEqual(value, round(value, 2), places=6)

    def test_market_is_hk(self) -> None:
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=100, price=10.0)
        self.assertIs(cost.market, Market.HK)
        self.assertIn("HK cost", cost.readable())


class UsCostTests(unittest.TestCase):
    """Verify the United States cost model (SP 2.38)."""

    def test_slippage_moves_buy_up_sell_down(self) -> None:
        config = CostConfig(slippage_bps=100)
        buy = us_order_cost(symbol="AAPL", side=_BUY, quantity=100, price=100.0, config=config)
        sell = us_order_cost(symbol="AAPL", side=_SELL, quantity=100, price=100.0, config=config)
        self.assertAlmostEqual(buy.exec_price, 101.0, places=4)
        self.assertAlmostEqual(sell.exec_price, 99.0, places=4)
        self.assertGreater(buy.slippage_cost, 0.0)
        self.assertGreater(sell.slippage_cost, 0.0)

    def test_regulatory_fee_is_sell_only(self) -> None:
        buy = us_order_cost(symbol="AAPL", side=_BUY, quantity=100, price=100.0)
        sell = us_order_cost(symbol="AAPL", side=_SELL, quantity=100, price=100.0)
        self.assertEqual(buy.regulatory_fee, 0.0)
        self.assertAlmostEqual(sell.regulatory_fee, 0.28, places=2)  # 0.0000278 * 10,000

    def test_total_cost_is_commission_plus_regulatory_plus_slippage(self) -> None:
        config = CostConfig(slippage_bps=100)
        sell = us_order_cost(symbol="AAPL", side=_SELL, quantity=100, price=100.0, config=config)
        self.assertIsInstance(sell, UsOrderCost)
        self.assertAlmostEqual(
            sell.total_cost,
            sell.commission + sell.regulatory_fee + sell.slippage_cost,
            places=2,
        )

    def test_market_is_us_and_readable(self) -> None:
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=100, price=10.0)
        self.assertIs(cost.market, Market.US)
        self.assertIn("US cost", cost.readable())


class LotFractionalTests(unittest.TestCase):
    """Verify the HK board-lot and US fractional-share rules (SP 2.37 / 2.38)."""

    def test_round_to_lot_rounds_down_to_whole_lots(self) -> None:
        self.assertEqual(round_to_lot(250), 200.0)
        self.assertEqual(round_to_lot(100), 100.0)
        self.assertEqual(round_to_lot(150, lot_size=50), 150.0)
        self.assertEqual(round_to_lot(0), 0.0)

    def test_round_to_lot_below_one_lot_is_zero(self) -> None:
        self.assertEqual(round_to_lot(99), 0.0)
        self.assertEqual(round_to_lot(50), 0.0)

    def test_round_to_lot_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "lot_size"):
            round_to_lot(100, lot_size=0)
        with self.assertRaisesRegex(ValueError, "quantity"):
            round_to_lot(-1)

    def test_round_to_fraction_keeps_fractional(self) -> None:
        self.assertEqual(round_to_fraction(123.45), 123.45)
        self.assertEqual(round_to_fraction(1.5), 1.5)

    def test_round_to_fraction_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            round_to_fraction(0)
        with self.assertRaisesRegex(ValueError, "positive"):
            round_to_fraction(-1)

    def test_hk_lot_buy_never_exceeds_intended_notional(self) -> None:
        quantity = round_to_lot(150)  # 100 shares
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=quantity, price=100.0)
        self.assertEqual(cost.quantity, 100.0)
        self.assertEqual(cost.notional, 10_000.0)

    def test_us_accepts_fractional_quantity(self) -> None:
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=100.5, price=100.0)
        self.assertEqual(cost.quantity, 100.5)
        self.assertAlmostEqual(cost.notional, 10_050.0, places=2)


class MinimumFeeTests(unittest.TestCase):
    """Verify the configured minimum commission in both markets (SP 2.37 / 2.38)."""

    def test_hk_min_commission_applies_when_rate_below_min(self) -> None:
        config = CostConfig(min_commission=10.0)
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=100, price=10.0, config=config)
        self.assertEqual(cost.commission, 10.0)  # 0.5 < 10

    def test_hk_commission_exceeds_min_when_notional_large(self) -> None:
        config = CostConfig(min_commission=10.0)
        cost = hk_order_cost(
            symbol="0001.HK", side=_BUY, quantity=10_000, price=100.0, config=config
        )
        self.assertEqual(cost.commission, 500.0)

    def test_us_min_commission_applies_when_rate_below_min(self) -> None:
        config = CostConfig(min_commission=5.0)
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=10, price=10.0, config=config)
        self.assertEqual(cost.commission, 5.0)  # 0.05 < 5

    def test_us_commission_exceeds_min_when_notional_large(self) -> None:
        config = CostConfig(min_commission=5.0)
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=10_000, price=100.0, config=config)
        self.assertEqual(cost.commission, 500.0)


class FxConversionTests(unittest.TestCase):
    """Verify FX conversion and multi-currency cash in the ledger (SP 2.42)."""

    def test_deposit_sets_acquisition_rate(self) -> None:
        ledger = _ledger()
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=7.8)
        self.assertAlmostEqual(ledger.balance(USD), 1_000.0, places=2)
        self.assertAlmostEqual(ledger.acquisition_rate(USD), 7.8, places=4)

    def test_deposit_updates_weighted_average_acquisition_rate(self) -> None:
        ledger = _ledger()
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=7.0)
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=8.0)
        self.assertAlmostEqual(ledger.acquisition_rate(USD), 7.5, places=4)

    def test_deposit_rejects_nonpositive_base_rate(self) -> None:
        with self.assertRaises(FxConversionError):
            deposit(_ledger(), currency=USD, amount=100.0, base_rate=0.0)

    def test_convert_foreign_to_base_books_fx_pnl(self) -> None:
        ledger = _ledger(base=HKD)
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=7.0)
        ledger = convert(ledger, from_currency=USD, to_currency=HKD, amount=1_000.0, rate=7.5)
        self.assertAlmostEqual(ledger.balance(HKD), 7_500.0, places=2)
        self.assertAlmostEqual(ledger.balance(USD), 0.0, places=2)
        self.assertAlmostEqual(ledger.fx_pnl, 500.0, places=2)  # 1000 * 0.5

    def test_convert_round_trip_at_same_rate_has_zero_pnl(self) -> None:
        ledger = _ledger(base=HKD)
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=7.0)
        ledger = convert(ledger, from_currency=USD, to_currency=HKD, amount=1_000.0, rate=7.0)
        self.assertAlmostEqual(ledger.fx_pnl, 0.0, places=2)

    def test_convert_base_to_foreign_sets_acquisition_rate(self) -> None:
        ledger = _ledger(base=HKD)
        ledger = deposit(ledger, currency=HKD, amount=7_800.0, base_rate=1.0)
        ledger = convert(ledger, from_currency=HKD, to_currency=USD, amount=7_800.0, rate=1 / 7.8)
        self.assertAlmostEqual(ledger.balance(USD), 1_000.0, places=2)
        self.assertAlmostEqual(ledger.acquisition_rate(USD), 7.8, places=4)

    def test_same_currency_conversion_is_refused(self) -> None:
        with self.assertRaisesRegex(FxConversionError, "distinct"):
            convert(_ledger(), from_currency=HKD, to_currency=HKD, amount=100.0, rate=1.0)

    def test_nonpositive_rate_is_refused(self) -> None:
        ledger = _ledger(base=HKD)
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=7.0)
        with self.assertRaises(FxConversionError):
            convert(ledger, from_currency=USD, to_currency=HKD, amount=100.0, rate=0.0)

    def test_insufficient_cash_is_refused(self) -> None:
        with self.assertRaises(InsufficientCashError):
            convert(_ledger(base=HKD), from_currency=USD, to_currency=HKD, amount=100.0, rate=7.0)

    def test_credit_does_not_change_acquisition_rate(self) -> None:
        ledger = _ledger()
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=7.0)
        ledger = credit(ledger, currency=USD, amount=500.0)
        self.assertAlmostEqual(ledger.balance(USD), 1_500.0, places=2)
        self.assertAlmostEqual(ledger.acquisition_rate(USD), 7.0, places=4)


class SuspensionTests(unittest.TestCase):
    """Verify suspension / untradeable handling (SP 2.41)."""

    def test_is_tradeable(self) -> None:
        self.assertTrue(is_tradeable(_quote(volume=1_000)))
        self.assertFalse(is_tradeable(_quote(volume=0)))
        self.assertFalse(is_tradeable(None))

    def test_refuse_order_no_quote(self) -> None:
        refused = refuse_order(order=_order(), day=_DAY, quote=None)
        self.assertIsInstance(refused, RefusedOrder)
        assert refused is not None
        self.assertIn("no quote", refused.reason)
        self.assertIn("suspended", refused.reason)
        self.assertIn("refused", refused.readable())

    def test_refuse_order_zero_volume(self) -> None:
        refused = refuse_order(order=_order(), day=_DAY, quote=_quote(volume=0))
        self.assertIsNotNone(refused)
        assert refused is not None
        self.assertIn("zero volume", refused.reason)

    def test_refuse_order_none_when_tradeable(self) -> None:
        self.assertIsNone(refuse_order(order=_order(), day=_DAY, quote=_quote()))

    def test_position_valued_at_close_when_quoted(self) -> None:
        valuation = position_valuation_price(
            market=Market.HK, symbol="0001.HK", day=_DAY, quote=_quote(close=11.0), last_quote=None
        )
        self.assertIsInstance(valuation, PositionValuation)
        self.assertEqual(valuation.price, 11.0)
        self.assertFalse(valuation.carried_forward)
        self.assertIsNone(valuation.warning)

    def test_position_carries_last_close_with_warning(self) -> None:
        valuation = position_valuation_price(
            market=Market.HK,
            symbol="0001.HK",
            day=_DAY,
            quote=None,
            last_quote=_quote(day=_LAST, close=11.5),
        )
        self.assertEqual(valuation.price, 11.5)
        self.assertTrue(valuation.carried_forward)
        self.assertIsInstance(valuation.warning, SuspensionWarning)
        assert valuation.warning is not None
        self.assertIn(_LAST.isoformat(), valuation.warning.message)
        self.assertIn("last available close", valuation.readable())

    def test_position_warning_suppressed_when_disabled(self) -> None:
        config = SuspensionConfig(warn=False)
        valuation = position_valuation_price(
            market=Market.HK,
            symbol="0001.HK",
            day=_DAY,
            quote=None,
            last_quote=_quote(day=_LAST, close=11.5),
            config=config,
        )
        self.assertTrue(valuation.carried_forward)
        self.assertIsNone(valuation.warning)

    def test_no_price_available_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "No price available"):
            position_valuation_price(
                market=Market.HK, symbol="0001.HK", day=_DAY, quote=None, last_quote=None
            )


if __name__ == "__main__":
    unittest.main()
