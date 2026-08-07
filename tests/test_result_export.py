"""Results JSON artifact tests (MVP 2 / SP 2.58).

Verifies that a backtest run exports as a stable, JSON-safe document covering
run metadata, config snapshot, net values, positions, trades, dividends,
corporate actions, refused orders, warnings and the optional research metrics
(SP 2.53–2.57).
"""

import json
import unittest
from datetime import date, timedelta
from types import MappingProxyType

from harbor.core.attribution import compute_attribution
from harbor.core.backtest_config import BacktestConfig, MarketQuota
from harbor.core.backtest_domain import (
    BacktestStatus,
    CashBalance,
    Currency,
    Market,
    NetValue,
    Order,
    OrderSide,
)
from harbor.core.backtest_runner import BacktestTrace, DailyResult
from harbor.core.backtest_state_machine import RunState
from harbor.core.corporate_actions import PositionAdjustment
from harbor.core.dividend_processing import CashDividend
from harbor.core.drawdown_events import compute_drawdown_events
from harbor.core.exposure import compute_exposure_series
from harbor.core.market_registry import CorporateActionType
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.result_export import (
    ExportError,
    _json_safe,
    export_run_to_dict,
    export_run_to_json,
)
from harbor.core.run_identity import RunIdentity
from harbor.core.suspension import RefusedOrder
from harbor.core.trade_metrics import TradeStats
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
    adjustments: tuple = (),
    refused: tuple = (),
    warnings: tuple = (),
) -> DailyResult:
    return DailyResult(
        as_of=as_of,
        valuation=valuation,
        fills=fills,
        dividends=dividends,
        adjustments=adjustments,
        refused=refused,
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


def _adjustment(*, cash: float = 0.0) -> PositionAdjustment:
    return PositionAdjustment(
        market=HK,
        symbol="0001.HK",
        action_id="a1",
        action_type=CorporateActionType.TENDER_OFFER,
        old_quantity=1_000.0,
        new_quantity=1_000.0,
        cash_amount=cash,
    )


def _refused(*, day: date) -> RefusedOrder:
    order = Order(
        symbol="0001.HK",
        market=HK,
        side=OrderSide.BUY,
        quantity=100.0,
        currency=HKD,
        trade_date=day,
    )
    return RefusedOrder(order=order, day=day, reason="suspended")


def _fx(_from: Currency, _to: Currency, _day: date) -> float | None:
    return None


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


class ArtifactStructureTests(unittest.TestCase):
    """Verify the top-level artifact sections (SP 2.58)."""

    def test_full_artifact_has_all_sections(self) -> None:
        artifact = export_run_to_dict(trace=_trace())
        for key in (
            "schema_version",
            "run",
            "config",
            "metrics",
            "net_values",
            "positions",
            "trades",
            "dividends",
            "corporate_actions",
            "refused",
            "warnings",
        ):
            self.assertIn(key, artifact)

    def test_run_metadata(self) -> None:
        run = export_run_to_dict(trace=_trace())["run"]
        self.assertEqual(run["run_id"], "r1")
        self.assertEqual(run["status"], "COMPLETED")
        self.assertTrue(run["succeeded"])
        inputs = run["inputs"]
        self.assertEqual(inputs["code_version"], "1.0.0")
        self.assertEqual(inputs["config_hash"], "abc123")
        self.assertEqual(inputs["data_cutoff"], _DAY.isoformat())
        self.assertEqual(inputs["data_range_start"], _DAY.isoformat())
        self.assertEqual(inputs["data_range_end"], _day(1).isoformat())
        self.assertEqual(run["base_currency"], "HKD")
        self.assertEqual(run["initial_capital"], _INITIAL)
        self.assertEqual(run["day_count"], 2)
        self.assertEqual(run["reconciliation_failures"], [])

    def test_config_snapshot(self) -> None:
        config = export_run_to_dict(trace=_trace())["config"]
        self.assertEqual(config["markets"], ["HK"])
        self.assertEqual(config["base_currency"], "HKD")
        self.assertEqual(config["initial_capital"], _INITIAL)
        self.assertEqual(
            config["market_quotas"], [{"market": "HK", "target_count": 15, "weight": 1.0}]
        )

    def test_net_values_entries(self) -> None:
        net_values = export_run_to_dict(trace=_trace())["net_values"]
        self.assertEqual(len(net_values), 2)
        self.assertEqual(net_values[0]["date"], _DAY.isoformat())
        self.assertEqual(net_values[0]["currency"], "HKD")
        self.assertAlmostEqual(net_values[0]["total_value"], 99_970.0, places=6)
        self.assertAlmostEqual(net_values[1]["total_value"], 105_970.0, places=6)

    def test_position_entries(self) -> None:
        positions = export_run_to_dict(trace=_trace())["positions"]
        self.assertEqual(len(positions), 2)
        first = positions[0]
        self.assertEqual(first["market"], "HK")
        self.assertEqual(first["symbol"], "0001.HK")
        self.assertEqual(first["quantity"], 1_000.0)
        self.assertEqual(first["price"], 50.0)
        self.assertFalse(first["carried_forward"])
        self.assertEqual(positions[1]["price"], 55.0)

    def test_trade_entries(self) -> None:
        trades = export_run_to_dict(trace=_trace())["trades"]
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade["side"], "BUY")
        self.assertAlmostEqual(trade["notional"], 50_000.0, places=6)
        self.assertEqual(trade["fee"], 30.0)
        self.assertEqual(trade["date"], _DAY.isoformat())

    def test_dividend_entries(self) -> None:
        dividends = export_run_to_dict(trace=_trace())["dividends"]
        self.assertEqual(len(dividends), 1)
        self.assertEqual(dividends[0]["gross_amount"], 1_000.0)
        self.assertFalse(dividends[0]["is_special"])
        self.assertEqual(dividends[0]["currency"], "HKD")

    def test_warning_entries(self) -> None:
        warnings = export_run_to_dict(trace=_trace())["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["date"], _DAY.isoformat())
        self.assertEqual(warnings[0]["message"], "research warning on day 0")

    def test_corporate_action_entries(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=50_000.0, positions=(_position_value(quantity=1_000.0, price=50.0),)
            ),
            fills=(_fill(quantity=1_000.0, price=50.0),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(
                cash=50_500.0, positions=(_position_value(quantity=1_000.0, price=50.0),)
            ),
            adjustments=(_adjustment(cash=500.0),),
        )
        entries = export_run_to_dict(trace=_trace((day0, day1)))["corporate_actions"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action_type"], "tender_offer")
        self.assertEqual(entries[0]["cash_amount"], 500.0)
        self.assertEqual(entries[0]["date"], _day(1).isoformat())

    def test_refused_entries(self) -> None:
        day0 = _result(
            as_of=_day(0), valuation=_valuation(cash=100_000.0), refused=(_refused(day=_day(0)),)
        )
        entries = export_run_to_dict(trace=_trace((day0,)))["refused"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["reason"], "suspended")
        self.assertEqual(entries[0]["symbol"], "0001.HK")
        self.assertEqual(entries[0]["side"], "BUY")


class MetricSectionTests(unittest.TestCase):
    """Verify the optional metrics sections (SP 2.53–2.57)."""

    def test_performance_section(self) -> None:
        metrics = PerformanceMetrics(
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
        section = export_run_to_dict(trace=_trace(), performance=metrics)["metrics"]["performance"]
        self.assertEqual(section["start_date"], _DAY.isoformat())
        self.assertEqual(section["cumulative_return"], 0.06)
        self.assertEqual(section["sharpe_ratio"], 0.25)

    def test_trade_stats_section(self) -> None:
        stats = TradeStats(
            fill_count=1,
            buy_count=1,
            sell_count=0,
            round_trip_count=0,
            win_count=0,
            win_rate=None,
            average_holding_days=None,
            turnover=None,
            total_fees_base=30.0,
            slippage_cost_base=0.0,
            unfilled_count=0,
            refused_reasons=MappingProxyType({"suspended": 1}),
        )
        section = export_run_to_dict(trace=_trace(), trade_stats=stats)["metrics"]["trade_stats"]
        self.assertEqual(section["fill_count"], 1)
        self.assertEqual(section["total_fees_base"], 30.0)
        self.assertEqual(section["refused_reasons"], {"suspended": 1})

    def test_exposure_section(self) -> None:
        results = _main_results()
        valuations = tuple(result.valuation for result in results)
        exposure = compute_exposure_series(valuations, fx_rate=_fx)
        section = export_run_to_dict(trace=_trace(), exposure=exposure)["metrics"]["exposure"]
        self.assertEqual(section["days"], 2)
        point = section["points"][0]
        self.assertEqual(point["as_of"], _DAY.isoformat())
        self.assertAlmostEqual(point["market_exposure"]["HK"], 50_000.0 / 99_970.0, places=6)
        self.assertAlmostEqual(point["currency_exposure"]["HKD"], 1.0, places=6)
        self.assertEqual(point["symbol_exposure"][0]["symbol"], "0001.HK")
        self.assertAlmostEqual(
            point["symbol_exposure"][0]["exposure"], 50_000.0 / 99_970.0, places=6
        )

    def test_drawdown_section(self) -> None:
        dip0 = _valuation(
            as_of=_day(0),
            cash=50_000.0,
            positions=(_position_value(quantity=1_000.0, price=50.0),),
        )
        dip1 = _valuation(
            as_of=_day(1),
            cash=50_000.0,
            positions=(_position_value(quantity=1_000.0, price=44.0),),
        )
        exposure = compute_exposure_series((dip0, dip1), fx_rate=_fx)
        drawdown = compute_drawdown_events((dip0, dip1), exposure=exposure)
        section = export_run_to_dict(trace=_trace(), drawdown=drawdown)["metrics"]["drawdown"]
        self.assertEqual(section["thresholds"], [0.05, 0.08, 0.10])
        self.assertEqual(len(section["events"]), 1)
        event = section["events"][0]
        self.assertEqual(event["threshold"], 0.05)
        self.assertAlmostEqual(event["depth"], 0.06, places=6)
        self.assertEqual(event["positions"][0]["symbol"], "0001.HK")
        self.assertEqual(event["positions"][0]["quantity"], 1_000.0)
        self.assertIsNotNone(event["exposure"])
        assert event["exposure"] is not None
        self.assertAlmostEqual(
            event["exposure"]["market_exposure"]["HK"], 44_000.0 / 94_000.0, places=6
        )

    def test_attribution_section(self) -> None:
        results = _main_results()
        report = compute_attribution(
            results, base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx
        )
        section = export_run_to_dict(trace=_trace(), attribution=report)["metrics"]["attribution"]
        self.assertTrue(section["reconciled"])
        self.assertAlmostEqual(section["totals"]["net_value_change"], 5_970.0, places=6)
        self.assertAlmostEqual(section["totals"]["price_return"], 5_000.0, places=6)
        self.assertAlmostEqual(section["totals"]["dividends"], 1_000.0, places=6)
        self.assertAlmostEqual(section["totals"]["trading_costs"], -30.0, places=6)
        self.assertEqual(len(section["days"]), 2)

    def test_missing_metrics_are_null(self) -> None:
        metrics = export_run_to_dict(trace=_trace())["metrics"]
        for key in ("performance", "trade_stats", "exposure", "drawdown", "attribution"):
            self.assertIsNone(metrics[key])


class JsonSerializationTests(unittest.TestCase):
    """Verify the JSON document output (SP 2.58)."""

    def test_json_round_trip(self) -> None:
        text = export_run_to_json(trace=_trace())
        self.assertEqual(json.loads(text), export_run_to_dict(trace=_trace()))

    def test_json_is_deterministic(self) -> None:
        first = export_run_to_json(trace=_trace())
        second = export_run_to_json(trace=_trace())
        self.assertEqual(first, second)

    def test_json_uses_iso_dates_and_enum_values(self) -> None:
        text = export_run_to_json(trace=_trace())
        self.assertIn(_DAY.isoformat(), text)
        self.assertNotIn("Market.HK", text)
        self.assertNotIn("datetime.date", text)


class BoundaryTests(unittest.TestCase):
    """Verify edge cases (SP 2.58)."""

    def test_empty_results_export(self) -> None:
        trace = _trace(results=())
        artifact = export_run_to_dict(trace=trace)
        self.assertEqual(artifact["run"]["day_count"], 0)
        self.assertIsNone(artifact["run"]["inputs"]["data_range_start"])
        self.assertIsNone(artifact["run"]["inputs"]["data_range_end"])
        self.assertEqual(artifact["net_values"], [])
        self.assertEqual(artifact["trades"], [])
        self.assertEqual(artifact["positions"], [])

    def test_unsafe_value_raises(self) -> None:
        with self.assertRaisesRegex(ExportError, "Cannot serialize"):
            _json_safe(object())


if __name__ == "__main__":
    unittest.main()
