"""Result consistency check tests (MVP 2 / SP 2.62).

Verifies that two runs of the same inputs produce identical net values, trades,
positions and metrics, that any difference is located precisely, and that a
replay-fingerprint mismatch (SP 2.61) is flagged even when sections line up.
"""

import unittest
from datetime import date, timedelta

from harbor.core.backtest_config import BacktestConfig, MarketQuota
from harbor.core.backtest_domain import (
    BacktestStatus,
    CashBalance,
    Currency,
    Market,
    NetValue,
    OrderSide,
)
from harbor.core.backtest_runner import BacktestTrace, DailyResult
from harbor.core.backtest_state_machine import RunState
from harbor.core.consistency_check import (
    ConsistencyError,
    ConsistencyIssue,
    ConsistencyReport,
    compare_artifacts,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.result_export import export_run_to_dict
from harbor.core.run_identity import RunIdentity
from harbor.core.valuation import DailyValuation, PositionValue

HKD = Currency.HKD
HK = Market.HK

_DAY = date(2024, 1, 2)
_INITIAL = 100_000.0


def _day(offset: int) -> date:
    return _DAY + timedelta(days=offset)


def _config() -> BacktestConfig:
    return BacktestConfig(
        markets=(Market.HK,),
        market_quotas=(MarketQuota(market=Market.HK, target_count=15, weight=1.0),),
        start_date=_DAY,
        end_date=_day(1),
        base_currency=HKD,
        initial_capital=_INITIAL,
    )


def _identity(code_version: str = "1.0.0") -> RunIdentity:
    return RunIdentity(config_hash="abc123", data_cutoff=_DAY, code_version=code_version)


def _state() -> RunState:
    return RunState(run_id="r1", status=BacktestStatus.COMPLETED)


def _position_value(price: float, quantity: float = 1_000.0) -> PositionValue:
    return PositionValue(
        market=HK,
        symbol="0001.HK",
        quantity=quantity,
        price=price,
        currency=HKD,
        fx_rate=1.0,
        market_value_quote=quantity * price,
        market_value_base=quantity * price,
        carried_forward=False,
        warning=None,
    )


def _valuation(*, as_of: date, cash: float, price: float, fees: float = 0.0) -> DailyValuation:
    position = _position_value(price)
    return DailyValuation(
        as_of=as_of,
        base_currency=HKD,
        cash=(CashBalance(currency=HKD, amount=cash),),
        position_values=(position,),
        realized_fees=(CashBalance(currency=HKD, amount=fees),),
        fx_pnl=0.0,
        net_value=NetValue(
            as_of_date=as_of,
            currency=HKD,
            cash=cash,
            securities_value=position.market_value_base,
            fees_paid=fees,
        ),
    )


def _result(
    *,
    as_of: date,
    valuation: DailyValuation,
    fills: tuple = (),
    dividends: tuple = (),
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


def _fill(*, quantity: float, price: float, fee: float = 0.0):
    from harbor.core.backtest_domain import Fill

    return Fill(
        order_ref="r",
        symbol="0001.HK",
        market=HK,
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        currency=HKD,
        trade_date=_DAY,
        fee=fee,
    )


def _dividend(*, gross: float = 0.0):
    from harbor.core.dividend_processing import CashDividend

    return CashDividend(
        market=HK,
        symbol="0001.HK",
        currency=HKD,
        entitlement_date=_DAY,
        payment_date=_DAY,
        quantity=100.0,
        per_share=gross / 100.0,
        gross_amount=gross,
        is_special=False,
    )


def _main_results() -> tuple[DailyResult, ...]:
    day0 = _result(
        as_of=_day(0),
        valuation=_valuation(as_of=_day(0), cash=_INITIAL - 50_030.0, price=50.0, fees=30.0),
        fills=(_fill(quantity=1_000.0, price=50.0, fee=30.0),),
    )
    day1 = _result(
        as_of=_day(1),
        valuation=_valuation(as_of=_day(1), cash=_INITIAL - 49_030.0, price=55.0, fees=30.0),
        dividends=(_dividend(gross=1_000.0),),
    )
    return (day0, day1)


def _trace(
    *,
    run_id: str = "r1",
    code_version: str = "1.0.0",
) -> BacktestTrace:
    return BacktestTrace(
        run_id=run_id,
        config=_config(),
        identity=_identity(code_version),
        state=_state(),
        results=_main_results(),
    )


def _artifact(
    *,
    run_id: str = "r1",
    code_version: str = "1.0.0",
    performance: PerformanceMetrics | None = None,
) -> dict:
    return export_run_to_dict(
        trace=_trace(run_id=run_id, code_version=code_version), performance=performance
    )


def _performance(cumulative: float = 0.06) -> PerformanceMetrics:
    return PerformanceMetrics(
        start_date=_DAY,
        end_date=_day(1),
        periods=2,
        cumulative_return=cumulative,
        annualized_return=0.05,
        annualized_volatility=0.2,
        max_drawdown=0.03,
        sharpe_ratio=0.25,
        calmar_ratio=1.5,
        downside_deviation=0.1,
    )


class IdenticalRunsTests(unittest.TestCase):
    """Verify identical runs are consistent (SP 2.62)."""

    def test_identical_artifacts_consistent(self) -> None:
        report = compare_artifacts(_artifact(), _artifact())
        self.assertIsInstance(report, ConsistencyReport)
        self.assertTrue(report.fingerprints_match)
        self.assertEqual(report.issues, ())
        self.assertTrue(report.consistent)

    def test_different_run_ids_still_consistent(self) -> None:
        report = compare_artifacts(_artifact(run_id="r1"), _artifact(run_id="r2"))
        self.assertTrue(report.fingerprints_match)
        self.assertTrue(report.consistent)


class DifferenceTests(unittest.TestCase):
    """Verify differences are located precisely (SP 2.62)."""

    def _compare_modified(self, mutate) -> ConsistencyReport:
        first = _artifact()
        second = _artifact()
        mutate(second)
        return compare_artifacts(first, second)

    def test_net_value_difference_located(self) -> None:
        def mutate(artifact: dict) -> None:
            artifact["net_values"][1]["total_value"] = 99_999.0

        report = self._compare_modified(mutate)
        self.assertFalse(report.consistent)
        self.assertEqual(len(report.issues), 1)
        issue = report.issues[0]
        self.assertIsInstance(issue, ConsistencyIssue)
        self.assertEqual(issue.section, "net_values")
        self.assertEqual(issue.location, "[1].total_value")
        self.assertEqual(issue.expected, "105970.0")
        self.assertEqual(issue.actual, "99999.0")

    def test_trade_difference_located(self) -> None:
        def mutate(artifact: dict) -> None:
            artifact["trades"][0]["fee"] = 55.0

        report = self._compare_modified(mutate)
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].location, "[0].fee")

    def test_position_difference_located(self) -> None:
        def mutate(artifact: dict) -> None:
            artifact["positions"][0]["quantity"] = 500.0

        report = self._compare_modified(mutate)
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].location, "[0].quantity")

    def test_metric_difference_located(self) -> None:
        first = _artifact(performance=_performance(cumulative=0.06))
        second = _artifact(performance=_performance(cumulative=0.09))
        report = compare_artifacts(first, second)
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].section, "metrics")
        self.assertEqual(report.issues[0].location, "performance.cumulative_return")

    def test_metric_present_vs_missing(self) -> None:
        first = _artifact(performance=_performance())
        second = _artifact()
        report = compare_artifacts(first, second)
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].location, "performance")

    def test_length_difference_located(self) -> None:
        def mutate(artifact: dict) -> None:
            artifact["net_values"] = artifact["net_values"][:1]

        report = self._compare_modified(mutate)
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].location, "length")
        self.assertEqual(report.issues[0].expected, "2")
        self.assertEqual(report.issues[0].actual, "1")


class FingerprintTests(unittest.TestCase):
    """Verify the SP 2.61 fingerprint gate (SP 2.62)."""

    def test_fingerprint_mismatch_flagged(self) -> None:
        first = _artifact()
        second = _artifact(code_version="2.0.0")
        report = compare_artifacts(first, second)
        self.assertFalse(report.fingerprints_match)
        self.assertFalse(report.consistent)

    def test_readable_lists_fingerprint_mismatch(self) -> None:
        report = compare_artifacts(_artifact(), _artifact(code_version="2.0.0"))
        self.assertIn("MISMATCH", report.readable())


class BoundaryTests(unittest.TestCase):
    """Verify edge cases (SP 2.62)."""

    def test_malformed_artifact_raises(self) -> None:
        with self.assertRaisesRegex(ConsistencyError, "SP 2.58"):
            compare_artifacts({}, _artifact())

    def test_readable_lists_issues(self) -> None:
        first = _artifact()
        second = _artifact()
        second["net_values"][1]["total_value"] = 99_999.0
        report = compare_artifacts(first, second)
        self.assertIn("[1].total_value", report.readable())
        self.assertIn("expected 105970.0", report.readable())
        self.assertIn("consistent: False", report.readable())

    def test_identical_readable(self) -> None:
        report = compare_artifacts(_artifact(), _artifact())
        self.assertIn("no differences", report.readable())
        self.assertIn("consistent: True", report.readable())


if __name__ == "__main__":
    unittest.main()
