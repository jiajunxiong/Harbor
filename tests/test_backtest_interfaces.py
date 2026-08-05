"""Backtest engine interface tests (MVP 2 / SP 2.3)."""

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
from datetime import date, timedelta

from harbor.core.backtest_domain import (
    BacktestState,
    BacktestStatus,
    CashBalance,
    Currency,
    Fill,
    Market,
    NetValue,
    Order,
    OrderSide,
    Position,
)
from harbor.core.backtest_interfaces import (
    AdjustmentFactor,
    BacktestDataReader,
    BacktestReport,
    BacktestReporter,
    DailyQuote,
    Dividend,
    FillSimulator,
    FundamentalRecord,
    PortfolioBuilder,
    SignalSource,
    TradingCalendar,
)
from harbor.core.equity import EntitlementEvent


def _quote(
    day: date,
    symbol: str = "AAPL",
    market: Market = Market.US,
    close: float = 100.0,
) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
        adjusted_close=close,
    )


class DataRecordTests(unittest.TestCase):
    """Verify the reader's contract value types."""

    def test_daily_quote_validation_and_immutability(self) -> None:
        quote = _quote(date(2026, 1, 2))
        self.assertEqual(quote.close, 100.0)
        with self.assertRaises(FrozenInstanceError):
            quote.close = 101.0  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "prices must be non-negative"):
            _quote(date(2026, 1, 2), close=-1.0)
        with self.assertRaisesRegex(ValueError, "volume must be non-negative"):
            DailyQuote(
                market=Market.US,
                symbol="AAPL",
                day=date(2026, 1, 2),
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=-1,
                adjusted_close=1.0,
            )

    def test_dividend_validation(self) -> None:
        dividend = Dividend(
            market=Market.HK,
            symbol="0005.HK",
            amount=1.5,
            currency=Currency.HKD,
            ex_date=date(2026, 3, 1),
            is_special=True,
        )
        self.assertTrue(dividend.is_special)
        with self.assertRaisesRegex(ValueError, "amount must be non-negative"):
            Dividend(
                market=Market.HK,
                symbol="0005.HK",
                amount=-1.0,
                currency=Currency.HKD,
                ex_date=date(2026, 3, 1),
            )

    def test_fundamental_record_allows_unknown_availability(self) -> None:
        record = FundamentalRecord(
            market=Market.US,
            symbol="AAPL",
            report_date=date(2025, 12, 31),
            fiscal_period="FY2025",
            available_on=None,
            roe=0.3,
        )
        self.assertIsNone(record.available_on)
        self.assertEqual(record.roe, 0.3)

    def test_adjustment_factor_requires_positive_factors(self) -> None:
        factor = AdjustmentFactor(
            market=Market.US,
            symbol="AAPL",
            date=date(2026, 1, 2),
            cumulative_factor=2.0,
            daily_factor=1.0,
        )
        self.assertEqual(factor.cumulative_factor, 2.0)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            AdjustmentFactor(
                market=Market.US,
                symbol="AAPL",
                date=date(2026, 1, 2),
                cumulative_factor=0.0,
                daily_factor=1.0,
            )

    def test_records_are_hashable(self) -> None:
        first = _quote(date(2026, 1, 2))
        second = _quote(date(2026, 1, 2))
        self.assertEqual(len({first, second}), 1)


class AbstractInterfaceTests(unittest.TestCase):
    """Abstract interfaces must not be directly instantiable."""

    def test_interfaces_cannot_be_instantiated(self) -> None:
        for interface in (
            BacktestDataReader,
            TradingCalendar,
            SignalSource,
            PortfolioBuilder,
            FillSimulator,
            BacktestReporter,
        ):
            with self.assertRaises(TypeError, msg=interface.__name__):
                interface()  # type: ignore[abstract]


class _FakeCalendar(TradingCalendar):
    def is_trading_day(self, market: Market, day: date) -> bool:
        return day.weekday() < 5

    def next_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day += timedelta(days=1)
        return day

    def previous_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day -= timedelta(days=1)
        return day

    def trading_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        days: list[date] = []
        day = start
        while day <= end:
            if self.is_trading_day(market, day):
                days.append(day)
            day += timedelta(days=1)
        return days

    def rebalance_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        return [d for d in self.trading_days(market, start, end) if d.day <= 5]


class _FakeReader(BacktestDataReader):
    def __init__(self, symbols: Sequence[str]) -> None:
        self._symbols = tuple(symbols)

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        # A delisted symbol is excluded after its last day.
        return [s for s in self._symbols if s != "DELISTED"]

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[DailyQuote]:
        return [_quote(end, symbol=symbol, market=market)]

    def dividends(self, market: Market, symbol: str, start: date, end: date) -> Sequence[Dividend]:
        return []

    def fundamentals(self, market: Market, symbol: str, as_of: date) -> Sequence[FundamentalRecord]:
        return [
            FundamentalRecord(
                market=market,
                symbol=symbol,
                report_date=date(2025, 12, 31),
                fiscal_period="FY2025",
                available_on=as_of,
            )
        ]

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[EntitlementEvent]:
        return []

    def adjustment_factors(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[AdjustmentFactor]:
        return []


class _FakeSignalSource(SignalSource):
    def signals(
        self, market: Market, decision_date: date, symbols: Sequence[str]
    ) -> dict[str, float]:
        return {symbol: float(len(symbol)) for symbol in symbols}


class _FakePortfolioBuilder(PortfolioBuilder):
    def target_positions(
        self,
        market: Market,
        decision_date: date,
        current: BacktestState,
        signals: Mapping[str, float],
    ) -> tuple[Position, ...]:
        keep = sorted(signals, key=lambda s: signals[s], reverse=True)[:2]
        return tuple(
            Position(
                symbol=s,
                market=market,
                quantity=1.0,
                average_cost=100.0,
                currency=Currency.USD,
                as_of_date=decision_date,
            )
            for s in keep
        )

    def order_drafts(
        self,
        market: Market,
        decision_date: date,
        current: BacktestState,
        target_positions: Sequence[Position],
        quotes: Mapping[str, DailyQuote],
    ) -> tuple[Order, ...]:
        held = {p.symbol for p in current.positions}
        return tuple(
            Order(
                symbol=p.symbol,
                market=market,
                side=OrderSide.BUY if p.symbol not in held else OrderSide.SELL,
                quantity=1.0,
                currency=Currency.USD,
                trade_date=decision_date,
                ref="rebalance",
            )
            for p in target_positions
        )


class _FakeFillSimulator(FillSimulator):
    def simulate(
        self,
        market: Market,
        day: date,
        orders: Sequence[Order],
        quotes: Mapping[str, DailyQuote],
    ) -> tuple[Fill, ...]:
        return tuple(
            Fill(
                order_ref=order.ref,
                symbol=order.symbol,
                market=order.market,
                side=order.side,
                quantity=order.quantity,
                price=quotes[order.symbol].close,
                currency=order.currency,
                trade_date=day,
                fee=1.0,
            )
            for order in orders
        )


class _FakeReporter(BacktestReporter):
    def render(self, report: BacktestReport) -> str:
        return (
            f"# Run {report.state.status.value}\n"
            f"positions={len(report.state.positions)} fills={len(report.fills)}"
        )


class PipelineContractTests(unittest.TestCase):
    """The interface contracts assemble into a working pipeline without DB/CLI."""

    def setUp(self) -> None:
        self.calendar = _FakeCalendar()
        self.reader = _FakeReader(["AAPL", "MSFT", "DELISTED"])
        self.signals = _FakeSignalSource()
        self.builder = _FakePortfolioBuilder()
        self.fills = _FakeFillSimulator()
        self.reporter = _FakeReporter()

    def test_data_reader_is_survivorship_free(self) -> None:
        universe = self.reader.list_securities(Market.US, date(2026, 1, 2))
        self.assertIn("AAPL", universe)
        self.assertNotIn("DELISTED", universe)

    def test_calendar_defers_to_next_trading_day(self) -> None:
        saturday = date(2026, 1, 3)
        self.assertFalse(self.calendar.is_trading_day(Market.US, saturday))
        self.assertEqual(self.calendar.next_trading_day(Market.US, saturday), date(2026, 1, 5))

    def test_full_pipeline_produces_typed_artifacts(self) -> None:
        start = date(2026, 1, 2)
        end = date(2026, 3, 31)
        state = BacktestState(
            status=BacktestStatus.INITIALIZING,
            as_of_date=start,
            cash=(CashBalance(currency=Currency.USD, amount=10_000.0),),
        )
        fills: list[Fill] = []

        for day in self.calendar.rebalance_days(Market.US, start, end):
            universe = self.reader.list_securities(Market.US, day)
            quotes = {
                symbol: self.reader.daily_quotes(Market.US, symbol, day, day)[0]
                for symbol in universe
            }
            scores = self.signals.signals(Market.US, day, universe)
            targets = self.builder.target_positions(Market.US, day, state, scores)
            drafts = self.builder.order_drafts(Market.US, day, state, targets, quotes)
            fills.extend(self.fills.simulate(Market.US, day, drafts, quotes))
            state = BacktestState(
                status=BacktestStatus.COMPLETED,
                as_of_date=day,
                positions=targets,
                cash=state.cash,
            )

        report = self.reporter.render(
            BacktestReport(state=state, fills=tuple(fills), net_values=())
        )
        self.assertIn("COMPLETED", report)
        self.assertIn("positions=2", report)
        self.assertIn("fills=", report)
        self.assertGreater(len(fills), 0)
        self.assertIsInstance(fills[0], Fill)
        self.assertEqual(fills[0].fee, 1.0)

    def test_net_value_snapshot_is_produced_by_valuation_contract(self) -> None:
        snapshot = NetValue(
            as_of_date=date(2026, 3, 31),
            currency=Currency.USD,
            cash=4_000.0,
            securities_value=6_000.0,
        )
        self.assertEqual(snapshot.total_value, 10_000.0)


if __name__ == "__main__":
    unittest.main()
