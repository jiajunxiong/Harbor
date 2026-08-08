"""Ledger reconciliation check tests (MVP 2 / SP 2.63).

Verifies that daily assets equal cash + position market values, and that the
cash change closes against sells, buys, fees, dividends, corporate-action cash
and FX; any inconsistency in the SP 2.58 artifact is located by day.
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
from harbor.core.corporate_actions import PositionAdjustment
from harbor.core.ledger_reconciliation import (
    DailyLedgerReconciliation,
    LedgerReconciliationReport,
    ReconciliationError,
    reconcile_ledger,
)
from harbor.core.market_registry import CorporateActionType
from harbor.core.result_export import export_run_to_dict
from harbor.core.run_identity import RunIdentity
from harbor.core.valuation import DailyValuation, PositionValue

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

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
    market: Market = HK,
    symbol: str = "0001.HK",
    quantity: float,
    price: float,
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
    as_of: date,
    cash: float,
    positions: tuple[PositionValue, ...] = (),
    fees: float = 0.0,
    fx_pnl: float = 0.0,
) -> DailyValuation:
    securities = sum(position.market_value_base for position in positions)
    return DailyValuation(
        as_of=as_of,
        base_currency=HKD,
        cash=(CashBalance(currency=HKD, amount=cash),),
        position_values=positions,
        realized_fees=(CashBalance(currency=HKD, amount=fees),),
        fx_pnl=fx_pnl,
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
) -> DailyResult:
    return DailyResult(
        as_of=as_of,
        valuation=valuation,
        fills=fills,
        dividends=dividends,
        adjustments=adjustments,
        refused=(),
        warnings=(),
    )


def _fill(
    *,
    market: Market = HK,
    symbol: str = "0001.HK",
    currency: Currency = HKD,
    quantity: float,
    price: float,
    fee: float = 0.0,
):
    from harbor.core.backtest_domain import Fill

    return Fill(
        order_ref="r",
        symbol=symbol,
        market=market,
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        currency=currency,
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


def _adjustment(*, symbol: str = "0001.HK", market: Market = HK, cash: float = 0.0):
    return PositionAdjustment(
        market=market,
        symbol=symbol,
        action_id="a1",
        action_type=CorporateActionType.TENDER_OFFER,
        old_quantity=1_000.0,
        new_quantity=1_000.0,
        cash_amount=cash,
    )


def _main_results() -> tuple[DailyResult, ...]:
    day0 = _result(
        as_of=_day(0),
        valuation=_valuation(
            as_of=_day(0),
            cash=_INITIAL - 50_030.0,
            positions=(_position_value(quantity=1_000.0, price=50.0),),
            fees=30.0,
        ),
        fills=(_fill(quantity=1_000.0, price=50.0, fee=30.0),),
    )
    day1 = _result(
        as_of=_day(1),
        valuation=_valuation(
            as_of=_day(1),
            cash=_INITIAL - 49_030.0,
            positions=(_position_value(quantity=1_000.0, price=55.0),),
            fees=30.0,
        ),
        dividends=(_dividend(gross=1_000.0),),
    )
    return (day0, day1)


def _us_results() -> tuple[DailyResult, ...]:
    day0 = _result(
        as_of=_day(0),
        valuation=_valuation(
            as_of=_day(0),
            cash=_INITIAL - 7_839.0,
            positions=(
                _position_value(
                    market=US, symbol="AAPL", quantity=10.0, price=100.0, currency=USD, fx_rate=7.8
                ),
            ),
            fees=39.0,
        ),
        fills=(_fill(market=US, symbol="AAPL", currency=USD, quantity=10.0, price=100.0, fee=5.0),),
    )
    return (day0,)


def _ca_results() -> tuple[DailyResult, ...]:
    day0 = _result(
        as_of=_day(0),
        valuation=_valuation(
            as_of=_day(0),
            cash=50_000.0,
            positions=(_position_value(quantity=1_000.0, price=50.0),),
        ),
        fills=(_fill(quantity=1_000.0, price=50.0),),
    )
    day1 = _result(
        as_of=_day(1),
        valuation=_valuation(
            as_of=_day(1),
            cash=50_500.0,
            positions=(_position_value(quantity=1_000.0, price=50.0),),
        ),
        adjustments=(_adjustment(cash=500.0),),
    )
    return (day0, day1)


def _trace(results: tuple[DailyResult, ...]) -> BacktestTrace:
    return BacktestTrace(
        run_id="r1",
        config=_config(),
        identity=_identity(),
        state=_state(),
        results=results,
    )


def _artifact(results: tuple[DailyResult, ...]) -> dict:
    return export_run_to_dict(trace=_trace(results))


def _fx(_from: Currency, _to: Currency, _day: date) -> float | None:
    return None


def _fx7_8(_from: Currency, _to: Currency, _day: date) -> float | None:
    if _from is USD and _to is HKD:
        return 7.8
    return None


class BalancedRunTests(unittest.TestCase):
    """Verify a well-formed run reconciles (SP 2.63)."""

    def test_balanced_run_reconciled(self) -> None:
        report = reconcile_ledger(_artifact(_main_results()), fx_rate=_fx)
        self.assertIsInstance(report, LedgerReconciliationReport)
        self.assertTrue(report.assets_reconciled)
        self.assertTrue(report.fees_reconciled)
        self.assertTrue(report.cash_reconciled)
        self.assertTrue(report.reconciled)
        self.assertEqual(len(report.days), 2)
        self.assertAlmostEqual(report.days[0].cash_gap, 0.0, places=6)
        self.assertAlmostEqual(report.days[1].cash_gap, 0.0, places=6)

    def test_corporate_action_cash_closes(self) -> None:
        report = reconcile_ledger(_artifact(_ca_results()), fx_rate=_fx)
        self.assertTrue(report.reconciled)
        self.assertAlmostEqual(report.days[1].corporate_actions, 500.0, places=6)

    def test_cross_market_fill_closes_with_fx(self) -> None:
        report = reconcile_ledger(_artifact(_us_results()), fx_rate=_fx7_8)
        self.assertTrue(report.reconciled)
        self.assertAlmostEqual(report.days[0].cash_change, -7_839.0, places=6)
        self.assertAlmostEqual(report.days[0].fees_expected, 39.0, places=6)


class ImbalanceTests(unittest.TestCase):
    """Verify inconsistencies are located by day (SP 2.63)."""

    def test_assets_imbalance_detected(self) -> None:
        artifact = _artifact(_main_results())
        artifact["net_values"][0]["cash"] = float(artifact["net_values"][0]["cash"]) + 100.0
        report = reconcile_ledger(artifact, fx_rate=_fx)
        self.assertFalse(report.days[0].assets_balanced)
        self.assertTrue(report.days[1].assets_balanced)
        self.assertFalse(report.reconciled)

    def test_cash_gap_detected_on_dividend(self) -> None:
        artifact = _artifact(_main_results())
        artifact["dividends"][0]["gross_amount"] = 2_000.0
        report = reconcile_ledger(artifact, fx_rate=_fx)
        self.assertTrue(report.days[0].cash_closes)
        self.assertFalse(report.days[1].cash_closes)
        self.assertAlmostEqual(report.days[1].cash_gap, -1_000.0, places=6)

    def test_fees_mismatch_detected(self) -> None:
        artifact = _artifact(_main_results())
        artifact["trades"][0]["fee"] = 55.0
        report = reconcile_ledger(artifact, fx_rate=_fx)
        self.assertFalse(report.days[0].fees_close)
        self.assertAlmostEqual(report.days[0].fees_expected, 55.0, places=6)
        self.assertAlmostEqual(report.days[0].fees_delta, 30.0, places=6)


class RefusalTests(unittest.TestCase):
    """Verify refusal on missing FX / untraceable currencies (SP 2.63)."""

    def test_missing_fx_refused(self) -> None:
        with self.assertRaisesRegex(ReconciliationError, "refusing to assume 1:1"):
            reconcile_ledger(_artifact(_us_results()), fx_rate=_fx)

    def test_ca_without_held_position_refused(self) -> None:
        artifact = _artifact(_main_results())
        artifact["corporate_actions"] = [
            {
                "date": _day(1).isoformat(),
                "market": "HK",
                "symbol": "X.HK",
                "action_id": "a1",
                "action_type": "tender_offer",
                "old_quantity": 0.0,
                "new_quantity": 0.0,
                "cash_amount": 100.0,
            }
        ]
        with self.assertRaisesRegex(ReconciliationError, "refusing to assume a currency"):
            reconcile_ledger(artifact, fx_rate=_fx)


class BoundaryTests(unittest.TestCase):
    """Verify edge cases (SP 2.63)."""

    def test_malformed_artifact_raises(self) -> None:
        with self.assertRaisesRegex(ReconciliationError, "SP 2.58"):
            reconcile_ledger({}, fx_rate=_fx)

    def test_empty_net_values_raises(self) -> None:
        artifact = _artifact(_main_results())
        artifact["net_values"] = []
        with self.assertRaisesRegex(ReconciliationError, "At least one net value"):
            reconcile_ledger(artifact, fx_rate=_fx)

    def test_out_of_order_raises(self) -> None:
        artifact = _artifact(_main_results())
        artifact["net_values"].reverse()
        with self.assertRaisesRegex(ReconciliationError, "ascending"):
            reconcile_ledger(artifact, fx_rate=_fx)

    def test_readable(self) -> None:
        report = reconcile_ledger(_artifact(_main_results()), fx_rate=_fx)
        text = report.readable()
        self.assertIn("ledger reconciliation", text)
        self.assertIn("reconciled: True", text)
        self.assertIn("ok", text)
        day = report.days[0]
        self.assertIsInstance(day, DailyLedgerReconciliation)
        self.assertIn("assets", day.readable())


if __name__ == "__main__":
    unittest.main()
