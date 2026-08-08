"""Small end-to-end backtest tests (MVP 2 / SP 2.51).

Runs the concrete end-to-end runner (:mod:`harbor.core.backtest_runner`) with
fixed Mock data for the three required combinations — HK-only, US-only and
cross-market (HK+US with an explicit HKD/USD rate) — and verifies that the run
completes, that every day reconciles (net value = cash + securities, position
value = qty x price x fx), that results are replayable (SP 2.48 / 2.62), and
that corporate actions and dividends flow through to cash and net value.
"""

import logging
import unittest
from datetime import date

from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_config import (
    BacktestConfig,
    MarketQuota,
    RebalanceFrequency,
    RiskConfig,
)
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import DailyQuote, Dividend
from harbor.core.backtest_runner import (
    BacktestTrace,
    MockUniverse,
    run_end_to_end_backtest,
)
from harbor.core.equity import EntitlementEvent
from harbor.core.market_registry import CorporateActionType
from harbor.core.run_logging import RunLogContext
from harbor.core.target_weight import TargetWeightConfig, WeightingMethod
from harbor.core.trading_calendar import MarketTradingCalendar

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_DAYS = (
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
)
_REBALANCE_DAY = _DAYS[0]
_DIVIDEND_DAY = _DAYS[2]
_ACTION_DAY = _DAYS[3]


def _calendar() -> MarketTradingCalendar:
    """Return a calendar with no holidays (weekdays trade)."""
    return MarketTradingCalendar({HK: frozenset(), US: frozenset()})


def _quote(
    *,
    market: Market,
    symbol: str,
    day: date,
    close: float,
    volume: int = 1_000_000,
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


def _hk_quotes() -> dict[tuple[Market, str], dict[date, DailyQuote]]:
    prices = {"0001.HK": 50.0, "0002.HK": 20.0}
    return {
        (HK, symbol): {day: _quote(market=HK, symbol=symbol, day=day, close=price) for day in _DAYS}
        for symbol, price in prices.items()
    }


def _us_quotes() -> dict[tuple[Market, str], dict[date, DailyQuote]]:
    prices = {"AAPL": 100.0, "MSFT": 200.0}
    return {
        (US, symbol): {day: _quote(market=US, symbol=symbol, day=day, close=price) for day in _DAYS}
        for symbol, price in prices.items()
    }


def _fx() -> dict[tuple[Currency, Currency], dict[date, float]]:
    return {(USD, HKD): {day: 7.8 for day in _DAYS}}


def _config(
    *,
    markets: tuple[Market, ...],
    quotas: tuple[MarketQuota, ...],
    base: Currency,
    initial_capital: float = 1_000_000.0,
) -> BacktestConfig:
    return BacktestConfig(
        markets=markets,
        market_quotas=quotas,
        start_date=_DAYS[0],
        end_date=_DAYS[-1],
        base_currency=base,
        rebalance_frequency=RebalanceFrequency.QUARTERLY,
        initial_capital=initial_capital,
        risk=RiskConfig(max_position_pct=1.0, max_market_pct=1.0, min_cash_pct=0.0),
    )


def _weighting(cash_weight: float = 0.05) -> TargetWeightConfig:
    return TargetWeightConfig(
        method=WeightingMethod.EQUAL, cash_weight=cash_weight, decimal_places=4
    )


class HkEndToEndTests(unittest.TestCase):
    """Run a HK-only portfolio end to end (SP 2.51)."""

    def setUp(self) -> None:
        self.config = _config(
            markets=(HK,),
            quotas=(MarketQuota(market=HK, target_count=2, weight=1.0),),
            base=HKD,
        )
        self.universe = MockUniverse(
            calendar=_calendar(),
            quotes=_hk_quotes(),
            selections={(HK, _REBALANCE_DAY): ("0001.HK", "0002.HK")},
        )

    def test_hk_run_completes_and_reconciles(self) -> None:
        trace = run_end_to_end_backtest(
            run_id="hk-1",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        self.assertIsInstance(trace, BacktestTrace)
        self.assertTrue(trace.succeeded)
        self.assertEqual(trace.reconcile_all(), ())
        self.assertEqual(len(trace.results), len(_DAYS))
        # Day-by-day: every net value = cash + securities.
        for result in trace.results:
            self.assertEqual(
                result.valuation.net_value.total_value,
                result.valuation.net_value.cash + result.valuation.net_value.securities_value,
            )

    def test_hk_opens_positions_on_rebalance_day(self) -> None:
        trace = run_end_to_end_backtest(
            run_id="hk-2",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        first = trace.results[0]
        self.assertGreaterEqual(len(first.fills), 2)
        self.assertTrue(trace.reconcile_all() == ())

    def test_hk_net_values_are_replayable(self) -> None:
        first = run_end_to_end_backtest(
            run_id="hk-3",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        second = run_end_to_end_backtest(
            run_id="hk-3",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        self.assertEqual(first.net_values(), second.net_values())
        self.assertEqual(first.identity.fingerprint(), second.identity.fingerprint())


class UsEndToEndTests(unittest.TestCase):
    """Run a US-only portfolio end to end (SP 2.51)."""

    def setUp(self) -> None:
        self.config = _config(
            markets=(US,),
            quotas=(MarketQuota(market=US, target_count=2, weight=1.0),),
            base=USD,
        )
        self.universe = MockUniverse(
            calendar=_calendar(),
            quotes=_us_quotes(),
            selections={(US, _REBALANCE_DAY): ("AAPL", "MSFT")},
        )

    def test_us_run_completes_and_reconciles(self) -> None:
        trace = run_end_to_end_backtest(
            run_id="us-1",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        self.assertTrue(trace.succeeded)
        self.assertEqual(trace.reconcile_all(), ())
        self.assertEqual(len(trace.results), len(_DAYS))

    def test_us_opens_positions_on_rebalance_day(self) -> None:
        trace = run_end_to_end_backtest(
            run_id="us-2",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        self.assertGreaterEqual(len(trace.results[0].fills), 2)
        self.assertTrue(trace.reconcile_all() == ())

    def test_us_net_values_are_replayable(self) -> None:
        first = run_end_to_end_backtest(
            run_id="us-3",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        second = run_end_to_end_backtest(
            run_id="us-3",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        self.assertEqual(first.net_values(), second.net_values())


class CrossMarketEndToEndTests(unittest.TestCase):
    """Run a HK+US cross-market portfolio end to end (SP 2.51)."""

    def setUp(self) -> None:
        self.config = _config(
            markets=(HK, US),
            quotas=(
                MarketQuota(market=HK, target_count=1, weight=0.5),
                MarketQuota(market=US, target_count=1, weight=0.5),
            ),
            base=HKD,
        )
        self.universe = MockUniverse(
            calendar=_calendar(),
            quotes={**_hk_quotes(), **_us_quotes()},
            fx_rates=_fx(),
            selections={
                (HK, _REBALANCE_DAY): ("0001.HK",),
                (US, _REBALANCE_DAY): ("AAPL",),
            },
        )

    def test_cross_market_run_completes_and_reconciles(self) -> None:
        trace = run_end_to_end_backtest(
            run_id="cm-1",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        self.assertTrue(trace.succeeded)
        self.assertEqual(trace.reconcile_all(), ())
        self.assertEqual(len(trace.results), len(_DAYS))

    def test_cross_market_us_position_valued_at_fx(self) -> None:
        trace = run_end_to_end_backtest(
            run_id="cm-2",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        day = trace.results[-1]
        us_values = [value for value in day.valuation.position_values if value.market is US]
        self.assertEqual(len(us_values), 1)
        value = us_values[0]
        self.assertAlmostEqual(value.fx_rate, 7.8, places=4)
        self.assertAlmostEqual(
            value.market_value_base, value.market_value_quote * value.fx_rate, places=2
        )

    def test_cross_market_missing_fx_refuses(self) -> None:
        # Removing the FX rate must refuse the cross-market valuation (SP 2.12).
        missing_fx = MockUniverse(
            calendar=_calendar(),
            quotes={**_hk_quotes(), **_us_quotes()},
            selections={
                (HK, _REBALANCE_DAY): ("0001.HK",),
                (US, _REBALANCE_DAY): ("AAPL",),
            },
        )
        trace = run_end_to_end_backtest(
            run_id="cm-3",
            config=self.config,
            universe=missing_fx,
            weighting=_weighting(),
        )
        self.assertFalse(trace.succeeded)
        # The merge (SP 2.27) refuses the cross-market combination without FX.
        self.assertIn(
            "cross-market combination is forbidden", trace.state.diagnostics.error_summary
        )


class CorporateActionFlowTests(unittest.TestCase):
    """Verify dividends and corporate actions flow into the end-to-end run."""

    def test_dividend_credits_cash_on_payment_day(self) -> None:
        config = _config(
            markets=(HK,),
            quotas=(MarketQuota(market=HK, target_count=1, weight=1.0),),
            base=HKD,
        )
        dividend = Dividend(
            market=HK,
            symbol="0001.HK",
            amount=1.0,
            currency=HKD,
            ex_date=_DIVIDEND_DAY,
            record_date=_DIVIDEND_DAY,
            payment_date=_DIVIDEND_DAY,
        )
        universe = MockUniverse(
            calendar=_calendar(),
            quotes=_hk_quotes(),
            dividends={(HK, "0001.HK"): (dividend,)},
            selections={(HK, _REBALANCE_DAY): ("0001.HK",)},
        )
        trace = run_end_to_end_backtest(
            run_id="dv-1",
            config=config,
            universe=universe,
            weighting=_weighting(),
        )
        self.assertTrue(trace.succeeded)
        self.assertEqual(trace.reconcile_all(), ())
        # The dividend was paid on _DIVIDEND_DAY; cash must jump by 1.0 x qty.
        cash_before = trace.results[1].valuation.net_value.cash
        cash_on_day = trace.results[2].valuation.net_value.cash
        self.assertGreater(cash_on_day, cash_before)
        self.assertTrue(any(d.symbol == "0001.HK" for d in trace.results[2].dividends))

    def test_split_changes_position_quantity(self) -> None:
        config = _config(
            markets=(US,),
            quotas=(MarketQuota(market=US, target_count=1, weight=1.0),),
            base=USD,
        )
        action = EntitlementEvent(
            action_id="split-1",
            action_type=CorporateActionType.SPLIT,
            terms=ActionTerms(ratio=2.0),
            record_date=_ACTION_DAY,
            ex_date=_ACTION_DAY,
        )
        universe = MockUniverse(
            calendar=_calendar(),
            quotes=_us_quotes(),
            corporate_actions={(US, "AAPL"): (action,)},
            selections={(US, _REBALANCE_DAY): ("AAPL",)},
        )
        trace = run_end_to_end_backtest(
            run_id="ca-1",
            config=config,
            universe=universe,
            weighting=_weighting(),
        )
        self.assertTrue(trace.succeeded)
        self.assertEqual(trace.reconcile_all(), ())
        adjustments = [
            adjustment
            for result in trace.results
            for adjustment in result.adjustments
            if adjustment.action_type is CorporateActionType.SPLIT
        ]
        self.assertEqual(len(adjustments), 1)
        self.assertTrue(adjustments[0].shares_changed)


class _CaptureHandler(logging.Handler):
    """Collects emitted records in memory for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture_logger() -> tuple[logging.Logger, _CaptureHandler]:
    """Return an isolated logger with a capturing handler."""
    logger = logging.getLogger("harbor.test_end_to_end_backtest")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _CaptureHandler()
    logger.addHandler(handler)
    return logger, handler


class RunLoggingRunnerTests(unittest.TestCase):
    """Verify SP 2.71 stage-correlation events from the end-to-end runner."""

    def setUp(self) -> None:
        self.config = _config(
            markets=(HK,),
            quotas=(MarketQuota(market=HK, target_count=2, weight=1.0),),
            base=HKD,
        )
        self.universe = MockUniverse(
            calendar=_calendar(),
            quotes=_hk_quotes(),
            selections={(HK, _REBALANCE_DAY): ("0001.HK", "0002.HK")},
        )

    def test_run_emits_stage_events_when_logger_supplied(self) -> None:
        logger, handler = _capture_logger()
        context = RunLogContext(run_id="log-1", strategy_version="1.0.0")
        trace = run_end_to_end_backtest(
            run_id="log-1",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
            log_context=context,
            logger=logger,
        )
        self.assertTrue(trace.succeeded)
        started = [
            record for record in handler.records if record.getMessage() == "backtest_stage_started"
        ]
        self.assertEqual(len(started), len(_DAYS) * 6)
        for record in started:
            self.assertEqual(record.backtest_run_id, "log-1")
            self.assertIn(
                record.stage,
                {"signal", "rebalance", "fill", "corporate_action", "valuation", "persist"},
            )
        self.assertIn("backtest_stage_completed", [r.getMessage() for r in handler.records])

    def test_run_without_logger_emits_nothing(self) -> None:
        _, handler = _capture_logger()
        run_end_to_end_backtest(
            run_id="log-2",
            config=self.config,
            universe=self.universe,
            weighting=_weighting(),
        )
        self.assertEqual(handler.records, [])


if __name__ == "__main__":
    unittest.main()
