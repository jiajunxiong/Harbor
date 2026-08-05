"""Data readiness precheck tests (MVP 2 / SP 2.13)."""

import unittest
from collections.abc import Callable, Sequence
from datetime import date, timedelta

from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_config import BacktestConfig, MarketQuota
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
    TradingCalendar,
)
from harbor.core.data_readiness import (
    PrecheckReport,
    PrecheckSeverity,
    run_precheck,
)
from harbor.core.equity import EntitlementEvent
from harbor.core.market_registry import CorporateActionType
from harbor.core.stock_pool import StockPool, StockPoolMembership


def _config(
    markets: tuple[Market, ...] = (Market.HK,),
    base_currency: Currency = Currency.HKD,
    start: date = date(2026, 1, 1),
    end: date = date(2026, 12, 31),
) -> BacktestConfig:
    quotas = tuple(
        MarketQuota(market=market, target_count=2, weight=1.0 / len(markets)) for market in markets
    )
    return BacktestConfig(
        markets=markets,
        market_quotas=quotas,
        start_date=start,
        end_date=end,
        base_currency=base_currency,
    )


class _FakeReader(BacktestDataReader):
    def __init__(
        self,
        *,
        quotes: bool = True,
        fundamentals: bool = True,
        corporate_actions: bool = True,
    ) -> None:
        self._quotes = quotes
        self._fundamentals = fundamentals
        self._corporate_actions = corporate_actions

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        return ("AAPL",)

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[DailyQuote]:
        if not self._quotes:
            return ()
        return [
            DailyQuote(
                market=market,
                symbol=symbol,
                day=start,
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=100,
                adjusted_close=1.0,
            )
        ]

    def dividends(self, market: Market, symbol: str, start: date, end: date) -> Sequence[Dividend]:
        return ()

    def fundamentals(self, market: Market, symbol: str, as_of: date) -> Sequence[FundamentalRecord]:
        if not self._fundamentals:
            return ()
        return [
            FundamentalRecord(
                market=market,
                symbol=symbol,
                report_date=date(2025, 12, 31),
                fiscal_period="FY2025",
                available_on=as_of,
                roe=0.3,
            )
        ]

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[EntitlementEvent]:
        if not self._corporate_actions:
            return ()
        return [
            EntitlementEvent(
                action_id="a-1",
                action_type=CorporateActionType.DIVIDEND,
                terms=ActionTerms(price=1.0),
                ex_date=start,
            )
        ]

    def adjustment_factors(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[object]:
        return ()


class _FakeCalendar(TradingCalendar):
    def __init__(self, *, trading: bool = True) -> None:
        self._trading = trading

    def is_trading_day(self, market: Market, day: date) -> bool:
        return self._trading and day.weekday() < 5

    def next_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day += timedelta(days=1)
        return day

    def previous_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day -= timedelta(days=1)
        return day

    def trading_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        if not self._trading:
            return ()
        days: list[date] = []
        cursor = self.next_trading_day(market, start)
        while cursor <= end:
            days.append(cursor)
            cursor = self.next_trading_day(market, cursor + timedelta(days=1))
        return days

    def rebalance_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        return self.trading_days(market, start, end)


def _pool(
    symbols: tuple[str, ...] = ("AAPL",),
    risk: bool = False,
    reason: str | None = None,
) -> StockPool:
    memberships = tuple(
        StockPoolMembership(
            market=Market.HK,
            symbol=symbol,
            effective_date=date(2010, 1, 1),
            expiry_date=None,
            source="hkex_universe",
        )
        for symbol in symbols
    )
    return StockPool(
        market=Market.HK,
        as_of=date(2026, 1, 1),
        source="hkex_universe",
        memberships=memberships,
        survivorship_bias_risk=risk,
        risk_reason=reason,
    )


class PrecheckReportTests(unittest.TestCase):
    """Verify the report contract and readable rendering."""

    def test_empty_report_reads_passed(self) -> None:
        report = PrecheckReport(())
        self.assertFalse(report.has_errors)
        self.assertIn("passed", report.readable())

    def test_errors_are_isolated(self) -> None:
        from harbor.core.data_readiness import PrecheckFinding

        report = PrecheckReport(
            (
                PrecheckFinding(PrecheckSeverity.ERROR, "HK", "boom"),
                PrecheckFinding(PrecheckSeverity.WARNING, "HK", "note"),
            )
        )
        self.assertTrue(report.has_errors)
        self.assertEqual(len(report.errors), 1)
        self.assertIn("[ERROR] HK: boom", report.readable())
        self.assertIn("Blocking errors found", report.readable())


class PrecheckTests(unittest.TestCase):
    """Verify each coverage check produces readable findings."""

    def _run(
        self,
        config: BacktestConfig,
        *,
        reader: BacktestDataReader,
        trading: bool = True,
        risk: bool = False,
        symbols: tuple[str, ...] = ("AAPL",),
        fx_rate: Callable[[Currency, Currency, date], float | None],
    ) -> PrecheckReport:
        return run_precheck(
            config,
            reader,
            _FakeCalendar(trading=trading),
            fx_rate=fx_rate,
            stock_pool=lambda market, as_of: _pool(symbols=symbols, risk=risk),
        )

    def test_clean_run_has_no_errors(self) -> None:
        report = self._run(
            _config(),
            reader=_FakeReader(quotes=True, fundamentals=True, corporate_actions=True),
            fx_rate=lambda f, t, d: 0.128,
        )
        self.assertFalse(report.has_errors)

    def test_empty_pool_is_an_error(self) -> None:
        report = self._run(
            _config(),
            reader=_FakeReader(),
            symbols=(),
            fx_rate=lambda f, t, d: 0.128,
        )
        self.assertTrue(report.has_errors)
        self.assertIn("stock pool is empty", report.readable())

    def test_survivorship_risk_is_a_warning(self) -> None:
        report = self._run(
            _config(),
            reader=_FakeReader(),
            risk=True,
            fx_rate=lambda f, t, d: 0.128,
        )
        self.assertFalse(report.has_errors)
        self.assertIn("survivorship-bias risk", report.readable())

    def test_missing_quotes_is_an_error(self) -> None:
        report = self._run(
            _config(),
            reader=_FakeReader(quotes=False),
            fx_rate=lambda f, t, d: 0.128,
        )
        self.assertTrue(report.has_errors)
        self.assertIn("no daily quotes", report.readable())

    def test_missing_fundamentals_is_a_warning(self) -> None:
        report = self._run(
            _config(),
            reader=_FakeReader(fundamentals=False, corporate_actions=True),
            fx_rate=lambda f, t, d: 0.128,
        )
        self.assertFalse(report.has_errors)
        self.assertIn("no point-in-time fundamentals", report.readable())

    def test_missing_fx_for_cross_currency_is_an_error(self) -> None:
        config = _config(markets=(Market.HK,), base_currency=Currency.USD)
        report = self._run(
            config,
            reader=_FakeReader(corporate_actions=True),
            fx_rate=lambda f, t, d: None,
        )
        self.assertTrue(report.has_errors)
        self.assertIn("missing FX HKD->USD", report.readable())
        self.assertIn("refusing to assume 1:1", report.readable())

    def test_fx_not_needed_for_same_currency(self) -> None:
        report = self._run(
            _config(base_currency=Currency.HKD),
            reader=_FakeReader(corporate_actions=True),
            fx_rate=lambda f, t, d: None,
        )
        self.assertFalse(report.has_errors)

    def test_missing_corporate_actions_is_a_warning(self) -> None:
        report = self._run(
            _config(),
            reader=_FakeReader(corporate_actions=False),
            fx_rate=lambda f, t, d: 0.128,
        )
        self.assertFalse(report.has_errors)
        self.assertIn("no corporate actions found", report.readable())

    def test_no_trading_days_is_an_error(self) -> None:
        report = self._run(
            _config(),
            reader=_FakeReader(),
            trading=False,
            fx_rate=lambda f, t, d: 0.128,
        )
        self.assertTrue(report.has_errors)
        self.assertIn("no trading days", report.readable())


if __name__ == "__main__":
    unittest.main()
