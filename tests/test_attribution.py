"""Attribution basics tests (MVP 2 / SP 2.57).

Verifies that the daily net-value change decomposes into price return,
dividends, corporate actions, trading costs and FX impact, and that the buckets
reconcile with the net-value change (对账) across buys, sells, price moves,
dividends, corporate-action cash, FX and quantity-only actions.
"""

import unittest
from collections.abc import Callable
from datetime import date, timedelta

from harbor.core.attribution import (
    AttributionError,
    AttributionReport,
    DailyAttribution,
    compute_attribution,
)
from harbor.core.backtest_domain import CashBalance, Currency, Fill, Market, NetValue, OrderSide
from harbor.core.backtest_runner import DailyResult
from harbor.core.corporate_actions import PositionAdjustment
from harbor.core.dividend_processing import CashDividend
from harbor.core.market_registry import CorporateActionType
from harbor.core.valuation import DailyValuation, PositionValue

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_DAY = date(2024, 1, 2)
_INITIAL = 100_000.0


def _day(offset: int) -> date:
    return _DAY + timedelta(days=offset)


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
    as_of: date = _DAY,
    valuation: DailyValuation,
    fills: tuple[Fill, ...] = (),
    dividends: tuple[CashDividend, ...] = (),
    adjustments: tuple[PositionAdjustment, ...] = (),
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
    symbol: str = "0001.HK",
    market: Market = HK,
    side: OrderSide = OrderSide.BUY,
    quantity: float,
    price: float,
    currency: Currency = HKD,
    fee: float = 0.0,
) -> Fill:
    return Fill(
        order_ref="r",
        symbol=symbol,
        market=market,
        side=side,
        quantity=quantity,
        price=price,
        currency=currency,
        trade_date=_DAY,
        fee=fee,
    )


def _dividend(*, currency: Currency = HKD, gross: float = 0.0) -> CashDividend:
    return CashDividend(
        market=HK,
        symbol="0001.HK",
        currency=currency,
        entitlement_date=_DAY,
        payment_date=_DAY,
        quantity=100.0,
        per_share=gross / 100.0,
        gross_amount=gross,
        is_special=False,
    )


def _adjustment(
    *,
    symbol: str = "0001.HK",
    market: Market = HK,
    action_type: CorporateActionType = CorporateActionType.TENDER_OFFER,
    old: float = 0.0,
    new: float = 0.0,
    cash: float = 0.0,
) -> PositionAdjustment:
    return PositionAdjustment(
        market=market,
        symbol=symbol,
        action_id="a1",
        action_type=action_type,
        old_quantity=old,
        new_quantity=new,
        cash_amount=cash,
    )


def _fx(rate: float | None) -> Callable[[Currency, Currency, date], float | None]:
    def get(_from: Currency, _to: Currency, _day: date) -> float | None:
        return rate

    return get


class ReconciliationTests(unittest.TestCase):
    """Verify the buckets sum to the net-value change (对账)."""

    def test_buy_only_day(self) -> None:
        result = _result(
            valuation=_valuation(
                cash=100_000.0 - 50_030.0,
                positions=(_position_value(quantity=1_000.0, price=50.0),),
                fees=30.0,
            ),
            fills=(_fill(quantity=1_000.0, price=50.0, fee=30.0),),
        )
        report = compute_attribution(
            (result,), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        day = report.days[0]
        self.assertIsInstance(day, DailyAttribution)
        self.assertAlmostEqual(day.net_value_change, -30.0, places=6)
        self.assertAlmostEqual(day.price_return, 0.0, places=6)
        self.assertAlmostEqual(day.trading_costs, -30.0, places=6)
        self.assertAlmostEqual(day.dividends, 0.0, places=6)
        self.assertAlmostEqual(day.corporate_actions, 0.0, places=6)
        self.assertAlmostEqual(day.fx_impact, 0.0, places=6)
        self.assertAlmostEqual(day.gap, 0.0, places=6)
        self.assertTrue(day.reconciled())

    def test_price_rise_day(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=100_000.0 - 50_030.0,
                positions=(_position_value(quantity=1_000.0, price=50.0),),
                fees=30.0,
            ),
            fills=(_fill(quantity=1_000.0, price=50.0, fee=30.0),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(
                cash=100_000.0 - 50_030.0,
                positions=(_position_value(quantity=1_000.0, price=55.0),),
                fees=30.0,
            ),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        self.assertAlmostEqual(report.days[1].price_return, 5_000.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 5_000.0, places=6)
        self.assertAlmostEqual(report.days[1].gap, 0.0, places=6)
        self.assertTrue(report.reconciled)

    def test_dividend_day(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=100_000.0 - 50_030.0,
                positions=(_position_value(quantity=1_000.0, price=50.0),),
                fees=30.0,
            ),
            fills=(_fill(quantity=1_000.0, price=50.0, fee=30.0),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(
                cash=100_000.0 - 50_030.0 + 1_000.0,
                positions=(_position_value(quantity=1_000.0, price=50.0),),
                fees=30.0,
            ),
            dividends=(_dividend(gross=1_000.0),),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        self.assertAlmostEqual(report.days[1].dividends, 1_000.0, places=6)
        self.assertAlmostEqual(report.days[1].price_return, 0.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 1_000.0, places=6)
        self.assertTrue(report.reconciled)

    def test_corporate_action_cash_day(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=100_000.0 - 50_030.0,
                positions=(_position_value(quantity=1_000.0, price=50.0),),
                fees=30.0,
            ),
            fills=(_fill(quantity=1_000.0, price=50.0, fee=30.0),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(
                cash=100_000.0 - 50_030.0 + 2_000.0,
                positions=(_position_value(quantity=1_000.0, price=50.0),),
                fees=30.0,
            ),
            adjustments=(_adjustment(old=1_000.0, new=1_000.0, cash=2_000.0),),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        self.assertAlmostEqual(report.days[1].corporate_actions, 2_000.0, places=6)
        self.assertAlmostEqual(report.days[1].price_return, 0.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 2_000.0, places=6)
        self.assertTrue(report.reconciled)

    def test_fx_cash_translation_day(self) -> None:
        day0 = _result(as_of=_day(0), valuation=_valuation(cash=780.0))
        day1 = _result(as_of=_day(1), valuation=_valuation(cash=790.0, fx_pnl=10.0))
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=780.0, fx_rate=_fx(None)
        )
        self.assertAlmostEqual(report.days[1].fx_impact, 10.0, places=6)
        self.assertAlmostEqual(report.days[1].price_return, 0.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 10.0, places=6)
        self.assertTrue(report.reconciled)

    def test_position_fx_reported_in_price_return(self) -> None:
        us_position = _position_value(
            symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD, fx_rate=7.8
        )
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(cash=0.0, positions=(us_position,)),
            fills=(_fill(symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD),),
        )
        us_position_up = _position_value(
            symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD, fx_rate=7.9
        )
        day1 = _result(as_of=_day(1), valuation=_valuation(cash=0.0, positions=(us_position_up,)))
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=7_800.0, fx_rate=_fx(7.8)
        )
        # The FX component of the position value is reported inside price return.
        self.assertAlmostEqual(report.days[1].price_return, 100.0, places=6)
        self.assertAlmostEqual(report.days[1].fx_impact, 0.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 100.0, places=6)
        self.assertTrue(report.reconciled)

    def test_split_is_value_preserving(self) -> None:
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
                cash=50_000.0, positions=(_position_value(quantity=2_000.0, price=25.0),)
            ),
            adjustments=(
                _adjustment(action_type=CorporateActionType.SPLIT, old=1_000.0, new=2_000.0),
            ),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        self.assertAlmostEqual(report.days[1].corporate_actions, 0.0, places=6)
        self.assertAlmostEqual(report.days[1].price_return, 0.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 0.0, places=6)
        self.assertTrue(report.reconciled)

    def test_sell_day_is_value_neutral(self) -> None:
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
                cash=70_000.0, positions=(_position_value(quantity=600.0, price=50.0),)
            ),
            fills=(_fill(side=OrderSide.SELL, quantity=400.0, price=50.0),),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        self.assertAlmostEqual(report.days[1].price_return, 0.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 0.0, places=6)
        self.assertTrue(report.reconciled)

    def test_full_run_totals_reconcile(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=100_000.0 - 50_030.0,
                positions=(_position_value(quantity=1_000.0, price=50.0),),
                fees=30.0,
            ),
            fills=(_fill(quantity=1_000.0, price=50.0, fee=30.0),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(
                cash=100_000.0 - 50_030.0 + 1_000.0,
                positions=(_position_value(quantity=1_000.0, price=55.0),),
                fees=30.0,
            ),
            dividends=(_dividend(gross=1_000.0),),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        self.assertIsInstance(report, AttributionReport)
        expected_change = sum(day.net_value_change for day in report.days)
        self.assertAlmostEqual(report.total_net_value_change, expected_change, places=6)
        total_buckets = (
            report.total_price_return
            + report.total_dividends
            + report.total_corporate_actions
            + report.total_trading_costs
            + report.total_fx_impact
        )
        self.assertAlmostEqual(total_buckets, report.total_net_value_change, places=6)
        self.assertAlmostEqual(report.total_gap, 0.0, places=6)
        self.assertTrue(report.reconciled)


class DividendTests(unittest.TestCase):
    """Verify the dividends bucket (SP 2.57 / 2.43)."""

    def test_foreign_dividend_converted_at_fx(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=92_200.0,
                positions=(
                    _position_value(
                        symbol="AAPL",
                        market=US,
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            fills=(_fill(symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(
                cash=92_278.0,
                positions=(
                    _position_value(
                        symbol="AAPL",
                        market=US,
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            dividends=(_dividend(currency=USD, gross=10.0),),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(7.8)
        )
        self.assertAlmostEqual(report.days[1].dividends, 78.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 78.0, places=6)
        self.assertTrue(report.reconciled)

    def test_missing_fx_dividend_refused(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=92_200.0,
                positions=(
                    _position_value(
                        symbol="AAPL",
                        market=US,
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            fills=(_fill(symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(
                cash=92_278.0,
                positions=(
                    _position_value(
                        symbol="AAPL",
                        market=US,
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            dividends=(_dividend(currency=USD, gross=10.0),),
        )
        with self.assertRaisesRegex(AttributionError, "refusing to assume 1:1"):
            compute_attribution(
                (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
            )


class CorporateActionTests(unittest.TestCase):
    """Verify the corporate-actions bucket (SP 2.57 / 2.44)."""

    def test_cash_action_uses_position_fx_rate(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=92_200.0,
                positions=(
                    _position_value(
                        symbol="AAPL",
                        market=US,
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            fills=(_fill(symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(
                cash=92_356.0,
                positions=(
                    _position_value(
                        symbol="AAPL",
                        market=US,
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            adjustments=(
                _adjustment(
                    symbol="AAPL",
                    market=US,
                    action_type=CorporateActionType.TENDER_OFFER,
                    old=10.0,
                    new=10.0,
                    cash=20.0,
                ),
            ),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(7.8)
        )
        self.assertAlmostEqual(report.days[1].corporate_actions, 156.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 156.0, places=6)
        self.assertTrue(report.reconciled)

    def test_cash_action_without_position_refused(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=92_200.0,
                positions=(
                    _position_value(
                        symbol="AAPL",
                        market=US,
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
            ),
            fills=(_fill(symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(cash=100_000.0),
            adjustments=(
                _adjustment(
                    symbol="AAPL",
                    market=US,
                    action_type=CorporateActionType.TENDER_OFFER,
                    cash=20.0,
                ),
            ),
        )
        with self.assertRaisesRegex(AttributionError, "refusing to assume a currency"):
            compute_attribution(
                (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(7.8)
            )

    def test_quantity_only_consolidation_is_zero(self) -> None:
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
                cash=50_000.0, positions=(_position_value(quantity=500.0, price=100.0),)
            ),
            adjustments=(
                _adjustment(
                    action_type=CorporateActionType.CONSOLIDATION,
                    old=1_000.0,
                    new=500.0,
                ),
            ),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        self.assertAlmostEqual(report.days[1].corporate_actions, 0.0, places=6)
        self.assertAlmostEqual(report.days[1].net_value_change, 0.0, places=6)
        self.assertTrue(report.reconciled)


class FillCostTests(unittest.TestCase):
    """Verify trading costs and foreign fill conversion (SP 2.57)."""

    def test_foreign_fill_notional_and_fee_converted(self) -> None:
        result = _result(
            valuation=_valuation(
                cash=100_000.0 - 7_800.0 - 39.0,
                positions=(
                    _position_value(
                        symbol="AAPL",
                        market=US,
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
                fees=39.0,
            ),
            fills=(
                _fill(symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD, fee=5.0),
            ),
        )
        report = compute_attribution(
            (result,), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(7.8)
        )
        day = report.days[0]
        self.assertAlmostEqual(day.price_return, 0.0, places=6)
        self.assertAlmostEqual(day.trading_costs, -39.0, places=6)
        self.assertAlmostEqual(day.net_value_change, -39.0, places=6)
        self.assertTrue(day.reconciled())

    def test_missing_fx_fill_refused(self) -> None:
        result = _result(
            valuation=_valuation(
                cash=92_161.0,
                positions=(
                    _position_value(
                        symbol="AAPL",
                        market=US,
                        quantity=10.0,
                        price=100.0,
                        currency=USD,
                        fx_rate=7.8,
                    ),
                ),
                fees=39.0,
            ),
            fills=(
                _fill(symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD, fee=5.0),
            ),
        )
        with self.assertRaisesRegex(AttributionError, "refusing to assume 1:1"):
            compute_attribution(
                (result,), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
            )

    def test_fees_accumulate_per_day(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(
                cash=50_000.0 - 30.0,
                positions=(_position_value(quantity=1_000.0, price=50.0),),
                fees=30.0,
            ),
            fills=(_fill(quantity=1_000.0, price=50.0, fee=30.0),),
        )
        day1 = _result(
            as_of=_day(1),
            valuation=_valuation(
                cash=49_970.0 - 25_040.0,
                positions=(_position_value(quantity=1_500.0, price=50.0),),
                fees=70.0,
            ),
            fills=(_fill(quantity=500.0, price=50.0, fee=40.0),),
        )
        report = compute_attribution(
            (day0, day1), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        self.assertAlmostEqual(report.days[0].trading_costs, -30.0, places=6)
        self.assertAlmostEqual(report.days[1].trading_costs, -40.0, places=6)
        self.assertTrue(report.reconciled)


class BoundaryTests(unittest.TestCase):
    """Verify refusal on invalid inputs (SP 2.57)."""

    def test_empty_results_rejected(self) -> None:
        with self.assertRaisesRegex(AttributionError, "At least one"):
            compute_attribution((), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None))

    def test_nonpositive_initial_capital_rejected(self) -> None:
        result = _result(valuation=_valuation(cash=0.0))
        with self.assertRaisesRegex(AttributionError, "initial_capital"):
            compute_attribution(
                (result,), base_currency=HKD, initial_capital=0.0, fx_rate=_fx(None)
            )

    def test_nonpositive_net_value_rejected(self) -> None:
        result = _result(valuation=_valuation(cash=0.0))
        with self.assertRaisesRegex(AttributionError, "positive"):
            compute_attribution(
                (result,), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
            )

    def test_out_of_order_rejected(self) -> None:
        later = _result(as_of=_day(1), valuation=_valuation(cash=10.0))
        earlier = _result(as_of=_day(0), valuation=_valuation(cash=10.0))
        with self.assertRaisesRegex(AttributionError, "ascending"):
            compute_attribution(
                (later, earlier), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
            )

    def test_readable_contains_fields(self) -> None:
        result = _result(valuation=_valuation(cash=100_000.0))
        report = compute_attribution(
            (result,), base_currency=HKD, initial_capital=_INITIAL, fx_rate=_fx(None)
        )
        self.assertIn("Attribution", report.readable())
        self.assertIn("price return", report.readable())
        self.assertIn("reconciled", report.readable())
        self.assertIn("change", report.days[0].readable())


if __name__ == "__main__":
    unittest.main()
