"""Metrics boundary tests (MVP 2 / SP 2.64).

A consolidated edge-case suite over the SP 2.52–2.57 metric modules covering
zero volatility (零波动), no trades (无交易), zero net value (净值为零), a
missing benchmark (缺失基准), cross-currency conversion (跨币种) and short
samples (短样本). Every boundary is either handled with an explicit metric or
refused with a dedicated error rather than fabricating a value.
"""

import unittest
from collections.abc import Callable
from datetime import date, timedelta

from harbor.core.attribution import AttributionError, compute_attribution
from harbor.core.backtest_config import BenchmarkComponent, BenchmarkConfig, BenchmarkKind
from harbor.core.backtest_domain import CashBalance, Currency, Fill, Market, NetValue, OrderSide
from harbor.core.backtest_runner import DailyResult
from harbor.core.benchmark import BenchmarkDataError, resolve_benchmark_series
from harbor.core.dividend_processing import CashDividend
from harbor.core.drawdown_events import DrawdownError, compute_drawdown_events
from harbor.core.exposure import ExposureError, ExposureSeries, compute_exposure_series
from harbor.core.performance_metrics import (
    MetricsConfig,
    MetricsError,
    annualized_volatility,
    calmar_ratio,
    compute_performance_metrics,
    cumulative_return,
    max_drawdown,
    sharpe_ratio,
)
from harbor.core.trade_metrics import TradeStatsError, compute_trade_stats
from harbor.core.valuation import DailyValuation, PositionValue

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_DAY = date(2024, 1, 2)


def _day(offset: int) -> date:
    """Return the backtest day ``offset`` trading days after the start."""
    return _DAY + timedelta(days=offset)


def _net(*values: float) -> tuple[NetValue, ...]:
    """Build a net-value series with one point per day (SP 2.53 style)."""
    return tuple(
        NetValue(
            as_of_date=_day(index),
            currency=HKD,
            cash=0.0,
            securities_value=value,
        )
        for index, value in enumerate(values)
    )


def _fx(rate: float | None) -> Callable[[Currency, Currency, date], float | None]:
    """Return an FX callable returning a fixed rate (or ``None``)."""

    def get(_from: Currency, _to: Currency, _day: date) -> float | None:
        return rate

    return get


def _fx_by_day(
    rates: dict[date, float | None],
) -> Callable[[Currency, Currency, date], float | None]:
    """Return an FX callable keyed by day, for attribution-style tests."""

    def get(_from: Currency, _to: Currency, day: date) -> float | None:
        return rates.get(day)

    return get


def _missing_level(_market: Market, _symbol: str, _day: date) -> float | None:
    """An index provider with no reliable data."""
    return None


def _zero_level(_market: Market, _symbol: str, _day: date) -> float | None:
    """An index provider returning a non-positive level."""
    return 0.0


def _position_value(
    *,
    market: Market = HK,
    symbol: str = "0001.HK",
    quantity: float = 100.0,
    price: float = 50.0,
    currency: Currency = HKD,
    fx_rate: float = 1.0,
) -> PositionValue:
    return PositionValue(
        market=market,
        symbol=symbol,
        quantity=quantity,
        price=price,
        currency=currency,
        fx_rate=fx_rate,
        market_value_quote=quantity * price,
        market_value_base=quantity * price * fx_rate,
        carried_forward=False,
        warning=None,
    )


def _valuation(
    *,
    as_of: date = _DAY,
    base: Currency = HKD,
    cash: tuple[CashBalance, ...] = (),
    positions: tuple[PositionValue, ...] = (),
    fees_paid: float = 0.0,
    fx_pnl: float = 0.0,
) -> DailyValuation:
    """Build a daily valuation; foreign cash is converted at a fixed 7.8."""
    cash_base = sum(balance.amount for balance in cash if balance.currency is base) + sum(
        balance.amount * 7.8 for balance in cash if balance.currency is not base
    )
    securities = sum(position.market_value_base for position in positions)
    return DailyValuation(
        as_of=as_of,
        base_currency=base,
        cash=cash,
        position_values=positions,
        realized_fees=(),
        fx_pnl=fx_pnl,
        net_value=NetValue(
            as_of_date=as_of,
            currency=base,
            cash=cash_base,
            securities_value=securities,
            fees_paid=fees_paid,
        ),
    )


def _fill(
    *,
    symbol: str = "AAPL",
    market: Market = US,
    side: OrderSide = OrderSide.BUY,
    quantity: float,
    price: float,
    currency: Currency = USD,
    day: date = _DAY,
) -> Fill:
    return Fill(
        order_ref="r",
        symbol=symbol,
        market=market,
        side=side,
        quantity=quantity,
        price=price,
        currency=currency,
        trade_date=day,
        fee=0.0,
    )


def _dividend(
    *,
    symbol: str = "AAPL",
    market: Market = US,
    currency: Currency = USD,
    gross: float = 0.0,
    day: date = _DAY,
    quantity: float = 10.0,
) -> CashDividend:
    return CashDividend(
        market=market,
        symbol=symbol,
        currency=currency,
        entitlement_date=day,
        payment_date=day,
        quantity=quantity,
        per_share=gross / quantity,
        gross_amount=gross,
        is_special=False,
    )


def _result(
    *,
    as_of: date = _DAY,
    valuation: DailyValuation,
    fills: tuple[Fill, ...] = (),
    dividends: tuple[CashDividend, ...] = (),
) -> DailyResult:
    return DailyResult(
        as_of=as_of,
        valuation=valuation,
        fills=fills,
        dividends=dividends,
        adjustments=(),
        refused=(),
        warnings=(),
    )


class ZeroVolatilityTests(unittest.TestCase):
    """零波动 — a flat net-value series has zero volatility; ratios are refused."""

    def test_annualized_volatility_flat_is_zero(self) -> None:
        series = _net(100.0, 100.0, 100.0)
        self.assertEqual(annualized_volatility(series, periods_per_year=252.0), 0.0)

    def test_max_drawdown_flat_is_zero(self) -> None:
        self.assertEqual(max_drawdown(_net(100.0, 100.0, 100.0)), 0.0)

    def test_sharpe_ratio_zero_volatility_refused(self) -> None:
        with self.assertRaisesRegex(MetricsError, "zero volatility"):
            sharpe_ratio(_net(100.0, 100.0, 100.0), periods_per_year=252.0)

    def test_calmar_ratio_zero_drawdown_refused(self) -> None:
        with self.assertRaisesRegex(MetricsError, "zero max drawdown"):
            calmar_ratio(_net(100.0, 100.0, 100.0), periods_per_year=252.0)

    def test_compute_performance_metrics_flat_refused(self) -> None:
        with self.assertRaisesRegex(MetricsError, "zero volatility"):
            compute_performance_metrics(
                _net(100.0, 100.0, 100.0), config=MetricsConfig(periods_per_year=252.0)
            )


class NoTradeTests(unittest.TestCase):
    """无交易 — a run with no fills has defined-but-empty trade and exposure metrics."""

    def test_no_fills_trade_stats(self) -> None:
        stats = compute_trade_stats((), base_currency=HKD, fx_rate=_fx(None))
        self.assertEqual(stats.fill_count, 0)
        self.assertEqual(stats.buy_count, 0)
        self.assertEqual(stats.sell_count, 0)
        self.assertEqual(stats.round_trip_count, 0)
        self.assertIsNone(stats.win_rate)
        self.assertIsNone(stats.average_holding_days)
        self.assertIsNone(stats.turnover)
        self.assertEqual(stats.total_fees_base, 0.0)
        self.assertEqual(stats.unfilled_count, 0)
        self.assertEqual(stats.refused_reasons, {})

    def test_no_fills_turnover_zero_with_net_values(self) -> None:
        stats = compute_trade_stats(
            (), base_currency=HKD, fx_rate=_fx(None), net_values=(1000.0, 1100.0)
        )
        self.assertEqual(stats.turnover, 0.0)

    def test_cash_only_exposure(self) -> None:
        valuation = _valuation(cash=(CashBalance(currency=HKD, amount=25_000.0),))
        point = compute_exposure_series((valuation,), fx_rate=_fx(None)).points[0]
        self.assertAlmostEqual(point.cash_exposure, 1.0, places=6)
        self.assertEqual(point.market_exposure, {})
        self.assertEqual(point.symbol_exposure, {})
        self.assertAlmostEqual(point.currency_exposure[HKD], 1.0, places=6)

    def test_attribution_no_trades_all_cash_reconciles(self) -> None:
        first = _result(
            as_of=_day(0),
            valuation=_valuation(
                as_of=_day(0), cash=(CashBalance(currency=HKD, amount=100_000.0),)
            ),
        )
        second = _result(
            as_of=_day(1),
            valuation=_valuation(
                as_of=_day(1), cash=(CashBalance(currency=HKD, amount=100_000.0),)
            ),
        )
        report = compute_attribution(
            (first, second), base_currency=HKD, initial_capital=100_000.0, fx_rate=_fx(None)
        )
        self.assertTrue(report.reconciled)
        self.assertAlmostEqual(report.total_price_return, 0.0, places=6)
        self.assertAlmostEqual(report.total_dividends, 0.0, places=6)
        self.assertEqual(report.days[0].gap, 0.0)


class ZeroNetValueTests(unittest.TestCase):
    """净值为零 — a zero net value is refused by every metric module."""

    def test_performance_zero_net_value_refused(self) -> None:
        with self.assertRaisesRegex(MetricsError, "positive"):
            cumulative_return(_net(100.0, 0.0, 110.0))

    def test_exposure_zero_total_refused(self) -> None:
        valuation = _valuation(cash=(CashBalance(currency=HKD, amount=0.0),))
        with self.assertRaisesRegex(ExposureError, "not positive"):
            compute_exposure_series((valuation,), fx_rate=_fx(None))

    def test_drawdown_zero_net_value_refused(self) -> None:
        valuations = (
            _valuation(as_of=_day(0), cash=(CashBalance(currency=HKD, amount=100.0),)),
            _valuation(as_of=_day(1), cash=(CashBalance(currency=HKD, amount=0.0),)),
        )
        with self.assertRaisesRegex(DrawdownError, "positive"):
            compute_drawdown_events(valuations)

    def test_attribution_zero_net_value_refused(self) -> None:
        first = _result(
            as_of=_day(0),
            valuation=_valuation(as_of=_day(0), cash=(CashBalance(currency=HKD, amount=100.0),)),
        )
        second = _result(
            as_of=_day(1),
            valuation=_valuation(as_of=_day(1), cash=(CashBalance(currency=HKD, amount=0.0),)),
        )
        with self.assertRaisesRegex(AttributionError, "positive"):
            compute_attribution(
                (first, second), base_currency=HKD, initial_capital=100.0, fx_rate=_fx(None)
            )


class MissingBenchmarkTests(unittest.TestCase):
    """缺失基准 — missing or non-positive index levels refuse, never fabricate."""

    _DAYS = (_day(0), _day(1), _day(2))

    def test_cash_benchmark_needs_no_data(self) -> None:
        series = resolve_benchmark_series(
            config=BenchmarkConfig(kind=BenchmarkKind.CASH),
            days=self._DAYS,
            index_level=_missing_level,
        )
        self.assertEqual(series.total_return(), 0.0)

    def test_missing_index_level_refused(self) -> None:
        config = BenchmarkConfig(kind=BenchmarkKind.MARKET_INDEX, market=HK, symbol="HSTECH")
        with self.assertRaisesRegex(BenchmarkDataError, "refusing to fabricate"):
            resolve_benchmark_series(config=config, days=self._DAYS, index_level=_missing_level)

    def test_zero_index_level_refused(self) -> None:
        config = BenchmarkConfig(kind=BenchmarkKind.MARKET_INDEX, market=HK, symbol="HSTECH")
        with self.assertRaisesRegex(BenchmarkDataError, "refusing to fabricate"):
            resolve_benchmark_series(config=config, days=self._DAYS, index_level=_zero_level)

    def test_blended_missing_leg_refused(self) -> None:
        config = BenchmarkConfig(
            kind=BenchmarkKind.BLENDED,
            cash_weight=0.5,
            components=(
                BenchmarkComponent(market=HK, symbol="HSTECH", weight=0.25),
                BenchmarkComponent(market=US, symbol="SPX", weight=0.25),
            ),
        )

        def missing_leg(market: Market, _symbol: str, _day: date) -> float | None:
            return None if market is US else 1.0

        with self.assertRaisesRegex(BenchmarkDataError, "refusing to fabricate a blended"):
            resolve_benchmark_series(config=config, days=self._DAYS, index_level=missing_leg)


class CrossCurrencyTests(unittest.TestCase):
    """跨币种 — foreign amounts convert at the day's FX rate; a missing rate refuses."""

    def test_trade_stats_us_fill_converts_at_rate(self) -> None:
        fill = _fill(symbol="AAPL", market=US, side=OrderSide.BUY, quantity=10.0, price=100.0)
        stats = compute_trade_stats((fill,), base_currency=HKD, fx_rate=_fx(7.8))
        self.assertEqual(stats.buy_count, 1)
        self.assertEqual(stats.total_fees_base, 0.0)

    def test_trade_stats_missing_fx_refused(self) -> None:
        fill = _fill(symbol="AAPL", market=US, side=OrderSide.BUY, quantity=10.0, price=100.0)
        with self.assertRaisesRegex(TradeStatsError, "refusing to assume 1:1"):
            compute_trade_stats((fill,), base_currency=HKD, fx_rate=_fx(None))

    def test_exposure_foreign_cash_missing_fx_refused(self) -> None:
        valuation = _valuation(cash=(CashBalance(currency=USD, amount=1_000.0),))
        with self.assertRaisesRegex(ExposureError, "refusing to assume 1:1"):
            compute_exposure_series((valuation,), fx_rate=_fx(None))

    def test_exposure_foreign_cash_converts_at_rate(self) -> None:
        valuation = _valuation(cash=(CashBalance(currency=USD, amount=1_000.0),))
        point = compute_exposure_series((valuation,), fx_rate=_fx(7.8)).points[0]
        self.assertAlmostEqual(point.cash_exposure, 1.0, places=6)
        self.assertAlmostEqual(point.currency_exposure[USD], 1.0, places=6)

    def test_attribution_foreign_dividend_missing_fx_refused(self) -> None:
        first = _result(
            as_of=_day(0),
            valuation=_valuation(
                as_of=_day(0), cash=(CashBalance(currency=HKD, amount=100_000.0),)
            ),
        )
        buy_day = _day(1)
        bought = _result(
            as_of=buy_day,
            valuation=_valuation(
                as_of=buy_day,
                cash=(CashBalance(currency=HKD, amount=92_200.0),),
                positions=(
                    _position_value(
                        market=US,
                        symbol="AAPL",
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            fills=(
                _fill(
                    symbol="AAPL",
                    market=US,
                    side=OrderSide.BUY,
                    quantity=10.0,
                    price=100.0,
                    day=buy_day,
                ),
            ),
        )
        pay_day = _day(2)
        paid = _result(
            as_of=pay_day,
            valuation=_valuation(
                as_of=pay_day,
                cash=(
                    CashBalance(currency=HKD, amount=92_200.0),
                    CashBalance(currency=USD, amount=100.0),
                ),
                positions=(
                    _position_value(
                        market=US,
                        symbol="AAPL",
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            dividends=(_dividend(gross=100.0, day=pay_day),),
        )
        with self.assertRaisesRegex(AttributionError, "refusing to assume 1:1"):
            compute_attribution(
                (first, bought, paid),
                base_currency=HKD,
                initial_capital=100_000.0,
                fx_rate=_fx_by_day({buy_day: 7.8, pay_day: None}),
            )

    def test_attribution_foreign_dividend_closes_at_rate(self) -> None:
        first = _result(
            as_of=_day(0),
            valuation=_valuation(
                as_of=_day(0), cash=(CashBalance(currency=HKD, amount=100_000.0),)
            ),
        )
        buy_day = _day(1)
        bought = _result(
            as_of=buy_day,
            valuation=_valuation(
                as_of=buy_day,
                cash=(CashBalance(currency=HKD, amount=92_200.0),),
                positions=(
                    _position_value(
                        market=US,
                        symbol="AAPL",
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            fills=(
                _fill(
                    symbol="AAPL",
                    market=US,
                    side=OrderSide.BUY,
                    quantity=10.0,
                    price=100.0,
                    day=buy_day,
                ),
            ),
        )
        pay_day = _day(2)
        paid = _result(
            as_of=pay_day,
            valuation=_valuation(
                as_of=pay_day,
                cash=(
                    CashBalance(currency=HKD, amount=92_200.0),
                    CashBalance(currency=USD, amount=100.0),
                ),
                positions=(
                    _position_value(
                        market=US,
                        symbol="AAPL",
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            dividends=(_dividend(gross=100.0, day=pay_day),),
        )
        report = compute_attribution(
            (first, bought, paid),
            base_currency=HKD,
            initial_capital=100_000.0,
            fx_rate=_fx_by_day({buy_day: 7.8, pay_day: 7.8}),
        )
        self.assertTrue(report.reconciled)
        self.assertAlmostEqual(report.total_dividends, 780.0, places=6)
        self.assertAlmostEqual(report.total_fx_impact, 0.0, places=6)


class ShortSampleTests(unittest.TestCase):
    """短样本 — too few points refuse with a dedicated error, or resolve to zero."""

    def test_performance_one_point_refused(self) -> None:
        with self.assertRaisesRegex(MetricsError, "At least two"):
            cumulative_return(_net(100.0))

    def test_performance_two_points_volatility_refused(self) -> None:
        self.assertAlmostEqual(cumulative_return(_net(100.0, 110.0)), 0.1, places=6)
        with self.assertRaisesRegex(MetricsError, "At least three"):
            annualized_volatility(_net(100.0, 110.0), periods_per_year=252.0)

    def test_drawdown_single_day_refused(self) -> None:
        valuation = _valuation(as_of=_day(0), cash=(CashBalance(currency=HKD, amount=100.0),))
        with self.assertRaisesRegex(DrawdownError, "At least two"):
            compute_drawdown_events((valuation,))

    def test_attribution_empty_refused(self) -> None:
        with self.assertRaisesRegex(AttributionError, "At least one"):
            compute_attribution((), base_currency=HKD, initial_capital=100_000.0, fx_rate=_fx(None))

    def test_exposure_empty_series_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one day"):
            ExposureSeries(points=())

    def test_benchmark_single_day_cash_total_return_zero(self) -> None:
        series = resolve_benchmark_series(
            config=BenchmarkConfig(kind=BenchmarkKind.CASH),
            days=(_day(0),),
            index_level=_missing_level,
        )
        self.assertEqual(series.total_return(), 0.0)
        self.assertEqual(series.returns(), ())


if __name__ == "__main__":
    unittest.main()
