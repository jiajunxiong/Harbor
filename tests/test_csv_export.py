"""CSV export tests (MVP 2 / SP 2.59).

Verifies that the SP 2.58 artifact renders as stable CSV documents for net
values, trades, positions, dividends, corporate actions, refused orders,
warnings, metrics and factor snapshots, each carrying ``backtest_run_id``.
"""

import csv
import io
import json
import unittest
from datetime import date, timedelta

from harbor.core.attribution import compute_attribution
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
from harbor.core.csv_export import (
    CsvExportError,
    export_all_csvs,
    export_factor_snapshot_csv,
    export_metrics_csv,
    export_net_values_csv,
    export_positions_csv,
    export_table_csv,
    export_trades_csv,
    export_warnings_csv,
)
from harbor.core.dividend_processing import CashDividend
from harbor.core.factor_snapshot import FactorSnapshot, FactorSnapshotEntry
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


def _identity() -> RunIdentity:
    return RunIdentity(config_hash="abc123", data_cutoff=_DAY, code_version="1.0.0")


def _state() -> RunState:
    return RunState(run_id="r1", status=BacktestStatus.COMPLETED)


def _position_value(
    *,
    symbol: str = "0001.HK",
    market: Market = HK,
    quantity: float = 0.0,
    price: float = 0.0,
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
    cash: float = 0.0,
    positions: tuple[PositionValue, ...] = (),
    fees: float = 0.0,
) -> DailyValuation:
    securities = sum(position.market_value_base for position in positions)
    return DailyValuation(
        as_of=as_of,
        base_currency=HKD,
        cash=(CashBalance(currency=HKD, amount=cash),),
        position_values=positions,
        realized_fees=(CashBalance(currency=HKD, amount=fees),),
        fx_pnl=0.0,
        net_value=NetValue(
            as_of_date=as_of,
            currency=HKD,
            cash=cash,
            securities_value=securities,
            fees_paid=fees,
        ),
    )


def _result(
    *,
    as_of: date,
    valuation: DailyValuation,
    fills: tuple = (),
    dividends: tuple = (),
    warnings: tuple = (),
) -> DailyResult:
    return DailyResult(
        as_of=as_of,
        valuation=valuation,
        fills=fills,
        dividends=dividends,
        adjustments=(),
        refused=(),
        warnings=warnings,
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


def _dividend(*, gross: float = 0.0) -> CashDividend:
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
        valuation=_valuation(
            cash=_INITIAL - 50_030.0,
            positions=(_position_value(quantity=1_000.0, price=50.0),),
            fees=30.0,
        ),
        fills=(_fill(quantity=1_000.0, price=50.0, fee=30.0),),
        warnings=("research warning on day 0",),
    )
    day1 = _result(
        as_of=_day(1),
        valuation=_valuation(
            cash=_INITIAL - 50_030.0 + 1_000.0,
            positions=(_position_value(quantity=1_000.0, price=55.0),),
            fees=30.0,
        ),
        dividends=(_dividend(gross=1_000.0),),
    )
    return (day0, day1)


def _trace(results: tuple[DailyResult, ...] | None = None) -> BacktestTrace:
    return BacktestTrace(
        run_id="r1",
        config=_config(),
        identity=_identity(),
        state=_state(),
        results=_main_results() if results is None else results,
    )


def _artifact(
    *,
    results: tuple[DailyResult, ...] | None = None,
    performance: PerformanceMetrics | None = None,
) -> dict:
    return export_run_to_dict(trace=_trace(results), performance=performance)


def _parse(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


class NetValueCsvTests(unittest.TestCase):
    """Verify the net-value CSV (净值)."""

    def test_header_and_run_id(self) -> None:
        text = export_net_values_csv(_artifact())
        rows = _parse(text)
        self.assertEqual(
            rows[0],
            [
                "backtest_run_id",
                "date",
                "currency",
                "cash",
                "securities_value",
                "fees_paid",
                "total_value",
                "fx_pnl",
            ],
        )
        self.assertEqual(len(rows), 3)  # header + 2 days
        self.assertTrue(all(row[0] == "r1" for row in rows[1:]))

    def test_values(self) -> None:
        rows = _parse(export_net_values_csv(_artifact()))
        self.assertEqual(rows[1][1], _DAY.isoformat())
        self.assertEqual(rows[1][2], "HKD")
        self.assertEqual(rows[1][6], "99970.0")
        self.assertEqual(rows[2][6], "105970.0")


class TableCsvTests(unittest.TestCase):
    """Verify the trade, position, dividend and warning CSVs."""

    def test_trades(self) -> None:
        rows = _parse(export_trades_csv(_artifact()))
        self.assertEqual(rows[0][0], "backtest_run_id")
        self.assertEqual(rows[1][5], "BUY")
        self.assertEqual(rows[1][9], "30.0")
        self.assertEqual(rows[1][10], "50000.0")

    def test_positions(self) -> None:
        rows = _parse(export_positions_csv(_artifact()))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][2], "HK")
        self.assertEqual(rows[1][3], "0001.HK")
        self.assertEqual(rows[1][4], "1000.0")
        self.assertEqual(rows[1][5], "50.0")
        self.assertEqual(rows[2][5], "55.0")

    def test_dividends(self) -> None:
        rows = _parse(export_table_csv(_artifact(), table="dividends"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "r1")
        self.assertEqual(rows[1][9], "1000.0")
        self.assertEqual(rows[1][10], "false")

    def test_warnings(self) -> None:
        rows = _parse(export_warnings_csv(_artifact()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], _DAY.isoformat())
        self.assertEqual(rows[1][2], "research warning on day 0")

    def test_empty_results_are_header_only(self) -> None:
        artifact = _artifact(results=())
        for table in ("net_values", "trades", "positions", "warnings"):
            rows = _parse(export_table_csv(artifact, table=table))
            self.assertEqual(len(rows), 1, table)


class MetricsCsvTests(unittest.TestCase):
    """Verify the metrics CSV (指标) long format."""

    def test_scalar_metric_rows(self) -> None:
        performance = PerformanceMetrics(
            start_date=_DAY,
            end_date=_day(1),
            periods=2,
            cumulative_return=0.06,
            annualized_return=0.05,
            annualized_volatility=0.2,
            max_drawdown=0.03,
            sharpe_ratio=0.25,
            calmar_ratio=1.5,
            downside_deviation=0.1,
        )
        text = export_metrics_csv(_artifact(performance=performance))
        rows = _parse(text)
        self.assertEqual(rows[0], ["backtest_run_id", "group", "field", "value"])
        flat = {(row[1], row[2]): row[3] for row in rows[1:]}
        self.assertEqual(flat[("performance", "cumulative_return")], "0.06")
        self.assertEqual(flat[("performance", "sharpe_ratio")], "0.25")
        self.assertEqual(flat[("performance", "start_date")], _DAY.isoformat())

    def test_nested_totals_flattened(self) -> None:
        results = _main_results()
        report = compute_attribution(
            results, base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx
        )
        artifact = export_run_to_dict(trace=_trace(), attribution=report)
        text = export_metrics_csv(artifact)
        rows = _parse(text)
        flat = {(row[1], row[2]): row[3] for row in rows[1:]}
        self.assertEqual(flat[("attribution", "totals.net_value_change")], "5970.0")
        self.assertEqual(flat[("attribution", "totals.dividends")], "1000.0")
        self.assertEqual(flat[("attribution", "reconciled")], "true")

    def test_missing_metrics_header_only(self) -> None:
        rows = _parse(export_metrics_csv(_artifact()))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], ["backtest_run_id", "group", "field", "value"])


class FactorSnapshotCsvTests(unittest.TestCase):
    """Verify the factor snapshot CSV (因子快照)."""

    def _snapshot(self) -> FactorSnapshot:
        entry = FactorSnapshotEntry(
            market=HK,
            symbol="0001.HK",
            raw_values=(("yield", 0.04), ("vol", 0.2)),
            availability_dates=(("fundamental", _DAY),),
            standardized_scores=(("yield", 0.8),),
            composite_score=0.7,
            rank=1,
            selected=True,
            exclusion_reason=None,
        )
        return FactorSnapshot(as_of=_DAY, entries=(entry,))

    def test_rows_and_run_id(self) -> None:
        text = export_factor_snapshot_csv((self._snapshot(),), run_id="r1")
        rows = _parse(text)
        self.assertEqual(rows[0][0], "backtest_run_id")
        self.assertEqual(rows[1][0], "r1")
        self.assertEqual(rows[1][1], _DAY.isoformat())
        self.assertEqual(rows[1][2], "HK")
        self.assertEqual(rows[1][3], "0001.HK")
        self.assertEqual(json.loads(rows[1][4]), {"yield": 0.04, "vol": 0.2})
        self.assertEqual(json.loads(rows[1][5]), {"fundamental": _DAY.isoformat()})
        self.assertEqual(json.loads(rows[1][6]), {"yield": 0.8})
        self.assertEqual(rows[1][7], "0.7")
        self.assertEqual(rows[1][8], "1")
        self.assertEqual(rows[1][9], "true")
        self.assertEqual(rows[1][10], "")

    def test_empty_snapshots_header_only(self) -> None:
        rows = _parse(export_factor_snapshot_csv((), run_id="r1"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "backtest_run_id")


class AllCsvTests(unittest.TestCase):
    """Verify the all-in-one export (SP 2.59)."""

    def test_all_keys_present(self) -> None:
        csves = export_all_csvs(_artifact(), factor_snapshots=())
        for key in (
            "net_values",
            "positions",
            "trades",
            "dividends",
            "corporate_actions",
            "refused",
            "warnings",
            "metrics",
            "factor_snapshots",
        ):
            self.assertIn(key, csves)
            self.assertTrue(csves[key].startswith("backtest_run_id"))


class BoundaryTests(unittest.TestCase):
    """Verify edge cases (SP 2.59)."""

    def test_unknown_table_raises(self) -> None:
        with self.assertRaisesRegex(CsvExportError, "Unknown CSV table"):
            export_table_csv(_artifact(), table="bogus")

    def test_csvs_are_parseable(self) -> None:
        for table in ("net_values", "trades", "positions", "metrics"):
            rows = _parse(
                export_table_csv(_artifact(), table=table)
                if table != "metrics"
                else export_metrics_csv(_artifact())
            )
            self.assertTrue(rows)


def _fx(_from: Currency, _to: Currency, _day: date) -> float | None:
    return None


if __name__ == "__main__":
    unittest.main()
