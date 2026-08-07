"""Trade and turnover metrics tests (MVP 2 / SP 2.54).

Verifies fill counts, win rate and holding period of closed round trips,
one-sided turnover, cumulative costs, slippage and unfilled order statistics,
and that a missing FX rate refuses base-currency conversion (never assume
1:1).
"""

import unittest
from collections.abc import Callable
from datetime import date

from harbor.core.backtest_domain import Currency, Fill, Market, Order, OrderSide
from harbor.core.suspension import RefusedOrder
from harbor.core.trade_metrics import (
    RoundTrip,
    TradeStats,
    TradeStatsError,
    _round_trips,
    compute_trade_stats,
)

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US
_BUY = OrderSide.BUY
_SELL = OrderSide.SELL

_D1 = date(2024, 1, 2)
_D2 = date(2024, 1, 3)
_D3 = date(2024, 1, 4)
_D4 = date(2024, 1, 5)


def _fill(
    *,
    side: OrderSide,
    symbol: str = "0001.HK",
    market: Market = HK,
    quantity: float = 100.0,
    price: float = 10.0,
    fee: float = 0.0,
    day: date = _D1,
    currency: Currency | None = None,
) -> Fill:
    ccy = currency if currency is not None else (HKD if market is HK else USD)
    return Fill(
        order_ref="r1",
        symbol=symbol,
        market=market,
        side=side,
        quantity=quantity,
        price=price,
        currency=ccy,
        trade_date=day,
        fee=fee,
    )


def _refused(*, reason: str = "suspended") -> RefusedOrder:
    return RefusedOrder(
        order=Order(
            symbol="0001.HK",
            market=HK,
            side=_BUY,
            quantity=100.0,
            currency=HKD,
            trade_date=_D1,
        ),
        day=_D1,
        reason=reason,
    )


def _fx(rate: float | None) -> Callable[[Currency, Currency, date], float | None]:
    def get(from_currency: Currency, to_currency: Currency, day: date) -> float | None:
        return rate

    return get


class FillCountTests(unittest.TestCase):
    """Verify the executed fill counts (成交次数)."""

    def test_counts_buys_and_sells(self) -> None:
        fills = (
            _fill(side=_BUY),
            _fill(side=_BUY, symbol="0002.HK", price=20.0),
            _fill(side=_SELL),
        )
        stats = compute_trade_stats(fills, base_currency=HKD, fx_rate=_fx(None))
        self.assertIsInstance(stats, TradeStats)
        self.assertEqual(stats.fill_count, 3)
        self.assertEqual(stats.buy_count, 2)
        self.assertEqual(stats.sell_count, 1)

    def test_empty_fills(self) -> None:
        stats = compute_trade_stats((), base_currency=HKD, fx_rate=_fx(None))
        self.assertEqual(stats.fill_count, 0)
        self.assertIsNone(stats.win_rate)
        self.assertIsNone(stats.turnover)


class RoundTripTests(unittest.TestCase):
    """Verify win rate and holding period (胜率 / 持有期)."""

    def test_round_trips_pair_buy_and_sell(self) -> None:
        fills = (
            _fill(side=_BUY, quantity=100.0, price=10.0, day=_D1),
            _fill(side=_SELL, quantity=100.0, price=12.0, day=_D4),
        )
        trips = _round_trips(fills)
        self.assertEqual(len(trips), 1)
        trip = trips[0]
        self.assertIsInstance(trip, RoundTrip)
        self.assertTrue(trip.profitable)
        self.assertEqual(trip.holding_days, 3)
        self.assertAlmostEqual(trip.pnl, 200.0, places=2)

    def test_fifo_round_trips(self) -> None:
        # Buy 100 @10, buy 100 @12, sell 150 @15: FIFO closes first lot fully.
        fills = (
            _fill(side=_BUY, quantity=100.0, price=10.0, day=_D1),
            _fill(side=_BUY, quantity=100.0, price=12.0, day=_D2),
            _fill(side=_SELL, quantity=150.0, price=15.0, day=_D3),
        )
        trips = _round_trips(fills)
        self.assertEqual(len(trips), 2)
        self.assertAlmostEqual(trips[0].pnl, 500.0, places=2)  # 100 * (15-10)
        self.assertAlmostEqual(trips[1].pnl, 150.0, places=2)  # 50 * (15-12)

    def test_win_rate(self) -> None:
        # Two profitable, one loss.
        fills = (
            _fill(side=_BUY, quantity=100.0, price=10.0, day=_D1),
            _fill(side=_SELL, quantity=100.0, price=12.0, day=_D2),  # win
            _fill(side=_BUY, quantity=100.0, price=10.0, day=_D1, symbol="0002.HK"),
            _fill(side=_SELL, quantity=100.0, price=8.0, day=_D2, symbol="0002.HK"),  # loss
        )
        stats = compute_trade_stats(fills, base_currency=HKD, fx_rate=_fx(None))
        self.assertEqual(stats.round_trip_count, 2)
        self.assertEqual(stats.win_count, 1)
        self.assertAlmostEqual(stats.win_rate, 0.5, places=6)

    def test_average_holding_days(self) -> None:
        fills = (
            _fill(side=_BUY, quantity=100.0, price=10.0, day=_D1),
            _fill(side=_SELL, quantity=100.0, price=12.0, day=_D2),  # 1 day
            _fill(side=_BUY, quantity=100.0, price=10.0, day=_D1, symbol="0002.HK"),
            _fill(side=_SELL, quantity=100.0, price=12.0, day=_D4, symbol="0002.HK"),  # 3 days
        )
        stats = compute_trade_stats(fills, base_currency=HKD, fx_rate=_fx(None))
        self.assertAlmostEqual(stats.average_holding_days, 2.0, places=6)


class CostAndSlippageTests(unittest.TestCase):
    """Verify cumulative costs and slippage (成本 / 滑点)."""

    def test_total_fees_base(self) -> None:
        fills = (
            _fill(side=_BUY, quantity=100.0, price=10.0, fee=5.0),
            _fill(side=_SELL, quantity=100.0, price=12.0, fee=6.0),
        )
        stats = compute_trade_stats(fills, base_currency=HKD, fx_rate=_fx(None))
        self.assertAlmostEqual(stats.total_fees_base, 11.0, places=2)

    def test_slippage_cost_for_us_fills(self) -> None:
        fills = (_fill(side=_BUY, symbol="AAPL", market=US, quantity=100.0, price=100.0),)
        stats = compute_trade_stats(fills, base_currency=USD, fx_rate=_fx(None), slippage_bps=100)
        # 100 * 100 * 100/10000 = 100 USD
        self.assertAlmostEqual(stats.slippage_cost_base, 100.0, places=2)

    def test_no_slippage_when_zero_bps(self) -> None:
        fills = (_fill(side=_BUY, symbol="AAPL", market=US, quantity=100.0, price=100.0),)
        stats = compute_trade_stats(fills, base_currency=USD, fx_rate=_fx(None))
        self.assertEqual(stats.slippage_cost_base, 0.0)


class TurnoverTests(unittest.TestCase):
    """Verify one-sided turnover (换手率)."""

    def test_turnover_min_of_buy_and_sell(self) -> None:
        fills = (
            _fill(side=_BUY, quantity=100.0, price=10.0, day=_D1),  # 1000 buy
            _fill(side=_SELL, quantity=100.0, price=12.0, day=_D2),  # 1200 sell
        )
        stats = compute_trade_stats(
            fills, base_currency=HKD, fx_rate=_fx(None), net_values=(100_000.0, 100_000.0)
        )
        self.assertAlmostEqual(stats.turnover, 1_000.0 / 100_000.0, places=6)

    def test_turnover_none_without_net_values(self) -> None:
        fills = (_fill(side=_BUY, quantity=100.0, price=10.0),)
        stats = compute_trade_stats(fills, base_currency=HKD, fx_rate=_fx(None))
        self.assertIsNone(stats.turnover)


class UnfilledOrderTests(unittest.TestCase):
    """Verify unfilled (refused) order statistics (未成交订单统计)."""

    def test_unfilled_count_and_reasons(self) -> None:
        refused = (
            _refused(reason="no quote"),
            _refused(reason="no quote"),
            _refused(reason="zero volume"),
        )
        stats = compute_trade_stats((), base_currency=HKD, fx_rate=_fx(None), refused=refused)
        self.assertEqual(stats.unfilled_count, 3)
        self.assertEqual(stats.refused_reasons["no quote"], 2)
        self.assertEqual(stats.refused_reasons["zero volume"], 1)

    def test_no_unfilled(self) -> None:
        stats = compute_trade_stats((), base_currency=HKD, fx_rate=_fx(None))
        self.assertEqual(stats.unfilled_count, 0)
        self.assertEqual(dict(stats.refused_reasons), {})


class FxConversionTests(unittest.TestCase):
    """Verify base-currency conversion refuses a missing FX rate (SP 2.12)."""

    def test_us_fill_converted_at_rate(self) -> None:
        fills = (_fill(side=_BUY, symbol="AAPL", market=US, quantity=100.0, price=100.0, fee=5.0),)
        stats = compute_trade_stats(fills, base_currency=HKD, fx_rate=_fx(7.8))
        # notional 10,000 USD -> 78,000 HKD; fee 5 USD -> 39 HKD.
        self.assertAlmostEqual(stats.total_fees_base, 39.0, places=2)

    def test_missing_fx_is_refused(self) -> None:
        fills = (_fill(side=_BUY, symbol="AAPL", market=US, quantity=100.0, price=100.0),)
        with self.assertRaisesRegex(TradeStatsError, "refusing to assume 1:1"):
            compute_trade_stats(fills, base_currency=HKD, fx_rate=_fx(None))

    def test_readable(self) -> None:
        fills = (
            _fill(side=_BUY, quantity=100.0, price=10.0),
            _fill(side=_SELL, quantity=100.0, price=12.0),
        )
        stats = compute_trade_stats(
            fills, base_currency=HKD, fx_rate=_fx(None), net_values=(100_000.0, 100_000.0)
        )
        self.assertIn("win rate", stats.readable())


if __name__ == "__main__":
    unittest.main()
