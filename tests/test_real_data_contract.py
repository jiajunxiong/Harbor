"""Real-data small-sample contract tests (MVP 2 / SP 2.83).

For Hong Kong and the United States, selects a small set of available symbols
and verifies the three acceptance dimensions:

- 数据可加载 (data can load): a small real yfinance sample loads and the
  standardized quote / dividend / financial rows match the provider contract;
- 预检 (precheck): the loaded sample feeds the SP 2.13 precheck, which produces
  a well-formed :class:`PrecheckReport`;
- 回测报告结构 (backtest report structure): the SP 2.60 HTML report renders the
  expected sections, disclaimer and embedded chart data.

Live network calls are wrapped so that any network / data-source failure skips
the test (网络失败可跳过), matching the established yfinance contract-test
pattern (SP 1.103 / 1.104); the report-structure check runs deterministically
over a small fixed artifact so it is never network-dependent.
"""

import importlib
import unittest
from collections.abc import Sequence
from datetime import date

from harbor.config import MarketTarget
from harbor.core.backtest_config import (
    BacktestConfig,
    MarketQuota,
    RebalanceFrequency,
    RiskConfig,
)
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.backtest_runner import MockUniverse, run_end_to_end_backtest
from harbor.core.data_readiness import PrecheckFinding, PrecheckReport, run_precheck
from harbor.core.html_report import render_html_report
from harbor.core.result_export import export_run_to_dict
from harbor.core.stock_pool import StockPool, StockPoolMembership
from harbor.core.target_weight import TargetWeightConfig, WeightingMethod
from harbor.core.trading_calendar import MarketTradingCalendar
from harbor.infrastructure.data_providers.yfinance import (
    HKYFinanceProvider,
    USYFinanceProvider,
)

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_QUOTE_START = date(2026, 1, 5)
_QUOTE_END = date(2026, 1, 9)
_DIVIDEND_START = date(2024, 1, 1)

_DAILY_KEYS = {
    "market",
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adjusted_close",
    "source",
}
_DIVIDEND_KEYS = {
    "market",
    "symbol",
    "ex_date",
    "record_date",
    "payment_date",
    "amount",
    "type",
    "currency",
}
_FINANCIAL_KEYS = {
    "market",
    "symbol",
    "report_date",
    "fiscal_period",
    "roe",
    "net_income",
    "total_equity",
    "revenue",
}

_SYMBOLS: dict[Market, str] = {
    HK: "0700.HK",
    US: "AAPL",
}


def _yfinance_available() -> bool:
    """Return whether the yfinance package can be imported."""
    try:
        importlib.import_module("yfinance")
    except ImportError:
        return False
    return True


def _provider(market: Market) -> object:
    """Return the yfinance provider for a market."""
    if market is HK:
        return HKYFinanceProvider()
    return USYFinanceProvider()


def _market_target(market: Market) -> MarketTarget:
    return MarketTarget(market.value)


def _quote_from_row(row: dict[str, object]) -> DailyQuote:
    return DailyQuote(
        market=Market(str(row["market"])),
        symbol=str(row["symbol"]),
        day=row["date"],  # type: ignore[arg-type]
        open=float(row["open"]),  # type: ignore[arg-type]
        high=float(row["high"]),  # type: ignore[arg-type]
        low=float(row["low"]),  # type: ignore[arg-type]
        close=float(row["close"]),  # type: ignore[arg-type]
        volume=int(row["volume"]),  # type: ignore[arg-type]
        adjusted_close=float(row["adjusted_close"]),  # type: ignore[arg-type]
    )


def _dividend_from_row(row: dict[str, object]) -> Dividend:
    return Dividend(
        market=Market(str(row["market"])),
        symbol=str(row["symbol"]),
        amount=float(row["amount"]),  # type: ignore[arg-type]
        currency=Currency(str(row["currency"])),
        ex_date=row["ex_date"],  # type: ignore[arg-type]
        record_date=row.get("record_date"),  # type: ignore[arg-type]
        payment_date=row.get("payment_date"),  # type: ignore[arg-type]
        is_special=str(row.get("type")) == "special",
    )


def _fundamental_from_row(row: dict[str, object]) -> FundamentalRecord:
    return FundamentalRecord(
        market=Market(str(row["market"])),
        symbol=str(row["symbol"]),
        report_date=row["report_date"],  # type: ignore[arg-type]
        fiscal_period=str(row["fiscal_period"]),
        available_on=row["report_date"],  # type: ignore[arg-type]
        roe=row.get("roe"),  # type: ignore[arg-type]
        net_income=row.get("net_income"),  # type: ignore[arg-type]
        total_equity=row.get("total_equity"),  # type: ignore[arg-type]
        revenue=row.get("revenue"),  # type: ignore[arg-type]
    )


class _SampleReader(BacktestDataReader):
    """In-memory reader over a fetched real-data sample (SP 2.83)."""

    def __init__(
        self,
        quotes: list[DailyQuote],
        dividends: list[Dividend],
        fundamentals: list[FundamentalRecord],
    ) -> None:
        self._quotes = quotes
        self._dividends = dividends
        self._fundamentals = fundamentals

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        return tuple(sorted({quote.symbol for quote in self._quotes if quote.market is market}))

    def daily_quotes(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[DailyQuote]:
        return tuple(
            quote
            for quote in self._quotes
            if quote.market is market and quote.symbol == symbol and start <= quote.day <= end
        )

    def dividends(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Dividend]:
        return tuple(
            dividend
            for dividend in self._dividends
            if dividend.market is market
            and dividend.symbol == symbol
            and start <= dividend.ex_date <= end
        )

    def fundamentals(
        self,
        market: Market,
        symbol: str,
        as_of: date,
    ) -> Sequence[FundamentalRecord]:
        return tuple(
            record
            for record in self._fundamentals
            if record.market is market and record.symbol == symbol
        )

    def corporate_actions(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[object]:
        return ()

    def adjustment_factors(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[object]:
        return ()


def _fetch_sample(
    test: unittest.TestCase, market: Market
) -> tuple[list[dict], list[dict], list[dict]]:
    """Fetch a small real sample; skip when network / data is unavailable."""
    provider = _provider(market)
    target = _market_target(market)
    symbol = _SYMBOLS[market]
    try:
        quotes = list(provider.fetch_daily_quotes(target, symbol, _QUOTE_START, _QUOTE_END))
        dividends = list(provider.fetch_dividends(target, symbol, _DIVIDEND_START, date.today()))
        financials = list(provider.fetch_financials(target, symbol))
    except Exception as error:  # pragma: no cover - network dependent
        test.skipTest(f"Live yfinance call failed: {error}")
    return quotes, dividends, financials


def _pool(market: Market, as_of: date) -> StockPool:
    """Return a sample stock pool containing the market's contract symbol."""
    symbol = _SYMBOLS[market]
    return StockPool(
        market=market,
        as_of=as_of,
        source="sample",
        memberships=(
            StockPoolMembership(
                market=market,
                symbol=symbol,
                effective_date=_QUOTE_START,
                expiry_date=None,
                source="sample",
            ),
        ),
        survivorship_bias_risk=False,
    )


@unittest.skipUnless(_yfinance_available(), "yfinance is not installed")
class LiveDataLoadTests(unittest.TestCase):
    """数据可加载: a small real sample matches the provider contract."""

    def _assert_quote_rows(self, rows: list[dict], market: Market) -> None:
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertEqual(set(row.keys()), _DAILY_KEYS)
            self.assertEqual(row["market"], market.value)
            self.assertEqual(row["symbol"], _SYMBOLS[market])
            self.assertEqual(row["source"], "yfinance")
            self.assertIsInstance(row["date"], date)
            for field in ("open", "high", "low", "close", "adjusted_close"):
                self.assertIsInstance(row[field], float)
                self.assertGreater(row[field], 0)
            self.assertIsInstance(row["volume"], int)
            self.assertGreaterEqual(row["volume"], 0)

    def _assert_dividend_rows(
        self, test: unittest.TestCase, rows: list[dict], market: Market
    ) -> None:
        if not rows:
            test.skipTest("No dividend data returned for the symbol.")
        for row in rows:
            self.assertEqual(set(row.keys()), _DIVIDEND_KEYS)
            self.assertEqual(row["market"], market.value)
            self.assertIsInstance(row["ex_date"], date)
            self.assertIsInstance(row["amount"], float)
            self.assertGreater(row["amount"], 0)

    def _assert_financial_rows(
        self, test: unittest.TestCase, rows: list[dict], market: Market
    ) -> None:
        if not rows:
            test.skipTest("No financial metrics returned for the symbol.")
        for row in rows:
            self.assertEqual(set(row.keys()), _FINANCIAL_KEYS)
            self.assertEqual(row["market"], market.value)
            self.assertEqual(row["symbol"], _SYMBOLS[market])
            self.assertIsInstance(row["report_date"], date)

    def test_hk_small_sample_loads(self) -> None:
        quotes, dividends, financials = _fetch_sample(self, HK)
        self._assert_quote_rows(quotes, HK)
        self._assert_dividend_rows(self, dividends, HK)
        self._assert_financial_rows(self, financials, HK)

    def test_us_small_sample_loads(self) -> None:
        quotes, dividends, financials = _fetch_sample(self, US)
        self._assert_quote_rows(quotes, US)
        self._assert_dividend_rows(self, dividends, US)
        self._assert_financial_rows(self, financials, US)


@unittest.skipUnless(_yfinance_available(), "yfinance is not installed")
class LivePrecheckTests(unittest.TestCase):
    """预检: the loaded sample produces a well-formed precheck report (2.13)."""

    def _precheck_over_sample(self, market: Market) -> PrecheckReport:
        quotes, dividends, fundamentals = _fetch_sample(self, market)
        reader = _SampleReader(
            [_quote_from_row(row) for row in quotes],
            [_dividend_from_row(row) for row in dividends],
            [_fundamental_from_row(row) for row in fundamentals],
        )
        base = HKD if market is HK else USD
        config = BacktestConfig(
            markets=(market,),
            market_quotas=(MarketQuota(market=market, target_count=1, weight=1.0),),
            start_date=_QUOTE_START,
            end_date=_QUOTE_END,
            base_currency=base,
            rebalance_frequency=RebalanceFrequency.QUARTERLY,
            initial_capital=1_000_000.0,
            risk=RiskConfig(max_position_pct=1.0, max_market_pct=1.0, min_cash_pct=0.0),
        )
        calendar = MarketTradingCalendar({HK: frozenset(), US: frozenset()})

        def fx_rate(from_currency: Currency, to_currency: Currency, as_of: date) -> float | None:
            return 1.0 if from_currency is to_currency else None

        return run_precheck(
            config,
            reader,
            calendar,
            fx_rate=fx_rate,
            stock_pool=_pool,
        )

    def test_precheck_over_hk_sample(self) -> None:
        report = self._precheck_over_sample(HK)
        self.assertIsInstance(report, PrecheckReport)
        self.assertIsInstance(report.findings, tuple)
        for finding in report.findings:
            self.assertIsInstance(finding, PrecheckFinding)
            self.assertTrue(finding.scope)
            self.assertTrue(finding.message)
        self.assertIsInstance(report.readable(), str)
        self.assertGreater(len(report.readable()), 0)
        # Same-currency sample: the precheck must not fabricate a missing-FX error.
        self.assertFalse(any("missing FX" in finding.message for finding in report.findings))

    def test_precheck_over_us_sample(self) -> None:
        report = self._precheck_over_sample(US)
        self.assertIsInstance(report, PrecheckReport)
        self.assertIsInstance(report.findings, tuple)
        for finding in report.findings:
            self.assertIsInstance(finding.scope, str)
            self.assertIsInstance(finding.message, str)
        self.assertIsInstance(report.readable(), str)


class ReportStructureTests(unittest.TestCase):
    """回测报告结构: the SP 2.60 HTML report has the expected shape."""

    _DAYS = (
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    )

    def _fixed_artifact(self) -> dict[str, object]:
        days = self._DAYS
        config = BacktestConfig(
            markets=(HK,),
            market_quotas=(MarketQuota(market=HK, target_count=2, weight=1.0),),
            start_date=days[0],
            end_date=days[-1],
            base_currency=HKD,
            rebalance_frequency=RebalanceFrequency.QUARTERLY,
            initial_capital=1_000_000.0,
            risk=RiskConfig(max_position_pct=1.0, max_market_pct=1.0, min_cash_pct=0.0),
        )
        calendar = MarketTradingCalendar({HK: frozenset(), US: frozenset()})
        quotes = {
            (HK, symbol): {
                day: DailyQuote(
                    market=HK,
                    symbol=symbol,
                    day=day,
                    open=50.0,
                    high=50.0,
                    low=50.0,
                    close=50.0,
                    volume=1_000_000,
                    adjusted_close=50.0,
                )
                for day in days
            }
            for symbol in ("0001.HK", "0002.HK")
        }
        universe = MockUniverse(
            calendar=calendar,
            quotes=quotes,
            selections={(HK, days[0]): ("0001.HK", "0002.HK")},
        )
        trace = run_end_to_end_backtest(
            run_id="contract-1",
            config=config,
            universe=universe,
            code_version="1.0.0",
            weighting=TargetWeightConfig(
                method=WeightingMethod.EQUAL,
                cash_weight=0.05,
                decimal_places=4,
            ),
        )
        return export_run_to_dict(trace=trace, schema_version="1.0")

    def test_html_report_has_expected_structure(self) -> None:
        html = render_html_report(self._fixed_artifact())
        self.assertIn("<html", html)
        self.assertIn("Backtest report contract-1", html)
        self.assertIn("摘要 (Summary)", html)
        self.assertIn("绩效指标 (Performance)", html)
        self.assertIn("主要风险 (Key Risks)", html)
        self.assertIn("数据覆盖 (Data Coverage)", html)
        self.assertIn("已知假设 (Known Assumptions)", html)
        self.assertIn("不构成投资建议", html)
        self.assertIn("window.REPORT_DATA", html)
        self.assertIn("net_values", html)

    def test_html_report_embeds_net_value_points(self) -> None:
        artifact = self._fixed_artifact()
        self.assertGreater(len(artifact["net_values"]), 0)
        html = render_html_report(artifact)
        self.assertIn("polyline", html)


if __name__ == "__main__":
    unittest.main()
