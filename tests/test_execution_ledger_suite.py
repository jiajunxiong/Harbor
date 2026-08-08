"""Execution and ledger test suite (MVP 2 / SP 2.80).

Runs the execution chain (SP 2.37-2.45): an order is priced per the fill rule
(SP 2.39), capped by the volume-participation rule (SP 2.40), refused or
carried when its symbol is suspended (SP 2.41), its all-in cost is computed by
the market's own cost model (SP 2.37 HK / 2.38 US), the fill moves cash in the
multi-currency ledger (SP 2.42) and the portfolio is valued in the base
currency with explicit FX (SP 2.45). It covers the acceptance matrix: HK/US
costs, FX, insufficient cash, suspension, partial fills and board
lots/fractional shares.

Unlike the SP 2.49 unit suite, these tests verify the interactions between the
stages (order -> fill -> ledger -> valuation), never a single stage in
isolation and never assuming a unified fee or rule across the two markets. The
suite is self-contained; no database is required.
"""

import unittest
from collections.abc import Callable
from datetime import date

from harbor.core.backtest_config import CostConfig, FillRule, UnfilledPolicy
from harbor.core.backtest_domain import Currency, Market, Order, OrderSide, Position
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.cost_hk import hk_order_cost, round_to_lot
from harbor.core.cost_us import round_to_fraction, us_order_cost
from harbor.core.fill_price import simulate_fill
from harbor.core.fx import FxConversionError
from harbor.core.ledger import (
    InsufficientCashError,
    apply_fill,
    convert,
    credit,
    deposit,
    empty_ledger,
)
from harbor.core.suspension import (
    PositionValuation,
    RefusedOrder,
    SuspensionWarning,
    is_tradeable,
    position_valuation_price,
    refuse_order,
)
from harbor.core.valuation import value_portfolio
from harbor.core.volume_limit import apply_volume_limit

HKD = Currency.HKD
USD = Currency.USD
_BUY = OrderSide.BUY
_SELL = OrderSide.SELL

_DAY = date(2024, 1, 2)
_DAY2 = date(2024, 1, 3)
_LAST = date(2023, 12, 29)


def _quote(
    *,
    day: date = _DAY,
    open_: float = 10.0,
    close: float = 10.0,
    volume: int = 1_000_000,
    market: Market = Market.HK,
    symbol: str = "0001.HK",
) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _order(
    *,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    side: OrderSide = _BUY,
    quantity: float = 800.0,
    currency: Currency = HKD,
) -> Order:
    return Order(
        symbol=symbol,
        market=market,
        side=side,
        quantity=quantity,
        currency=currency,
        trade_date=_DAY,
        ref="r1",
    )


def _position(
    *,
    symbol: str = "AAPL",
    market: Market = Market.US,
    quantity: float = 10.0,
    currency: Currency = USD,
) -> Position:
    return Position(
        symbol=symbol,
        market=market,
        quantity=quantity,
        average_cost=100.0,
        currency=currency,
        as_of_date=_DAY,
    )


def _fx(rate: float | None) -> Callable[[Currency, Currency, date], float | None]:
    """Return an FX callable returning ``rate`` for every non-base leg."""

    def fx_rate(from_currency: Currency, to_currency: Currency, as_of: date) -> float | None:
        if from_currency is to_currency:
            return 1.0
        return rate

    return fx_rate


class HkCostExecutionTests(unittest.TestCase):
    """HK execution: all-in fee, both-sides symmetry and the fill rule (2.37)."""

    def test_hk_fill_fee_is_all_in_and_cash_debited(self) -> None:
        fill = simulate_fill(
            order=_order(quantity=800.0),
            rule=FillRule.CLOSE,
            quote=_quote(close=10.0),
        )
        expected = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800.0, price=10.0)
        self.assertEqual(fill.price, 10.0)
        self.assertAlmostEqual(fill.fee, expected.total_fee, places=2)
        # The fee is the sum of the four HK components (SP 2.37).
        self.assertAlmostEqual(
            expected.total_fee,
            expected.commission
            + expected.stamp_duty
            + expected.transaction_levy
            + expected.trading_fee,
            places=2,
        )
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=HKD,
            amount=100_000.0,
            base_rate=1.0,
        )
        ledger = apply_fill(ledger, fill=fill)
        self.assertAlmostEqual(ledger.balance(HKD), 100_000.0 - 8_000.0 - fill.fee, places=2)
        self.assertAlmostEqual(ledger.fees(HKD), fill.fee, places=2)

    def test_hk_buy_and_sell_share_the_same_cost(self) -> None:
        buy = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800.0, price=10.0)
        sell = hk_order_cost(symbol="0001.HK", side=_SELL, quantity=800.0, price=10.0)
        self.assertEqual(buy.total_fee, sell.total_fee)
        self.assertAlmostEqual(sell.total_fee, buy.total_fee, places=2)

    def test_hk_fill_price_follows_the_rule(self) -> None:
        quote = _quote(open_=9.8, close=10.0)
        next_quote = _quote(day=_DAY2, open_=10.2, close=10.4)
        order = _order(quantity=100.0)
        self.assertEqual(
            simulate_fill(order=order, rule=FillRule.OPEN, quote=quote).price,
            quote.open,
        )
        close_fill = simulate_fill(order=order, rule=FillRule.CLOSE, quote=quote)
        self.assertEqual(close_fill.price, quote.close)
        self.assertEqual(close_fill.trade_date, _DAY)
        next_fill = simulate_fill(
            order=order,
            rule=FillRule.NEXT_OPEN,
            quote=quote,
            next_quote=next_quote,
        )
        self.assertEqual(next_fill.price, next_quote.open)
        self.assertEqual(next_fill.trade_date, _DAY2)


class UsCostExecutionTests(unittest.TestCase):
    """US execution: slippage inside the fee, SELL-only regulatory fee (2.38)."""

    def test_us_buy_slippage_moves_exec_price_but_not_fill_price(self) -> None:
        config = CostConfig(slippage_bps=100)
        fill = simulate_fill(
            order=_order(symbol="AAPL", market=Market.US, quantity=10.0, currency=USD),
            rule=FillRule.CLOSE,
            quote=_quote(market=Market.US, symbol="AAPL", close=100.0),
            config=config,
        )
        cost = us_order_cost(
            symbol="AAPL",
            side=_BUY,
            quantity=10.0,
            price=100.0,
            config=config,
        )
        self.assertEqual(cost.exec_price, 101.0)  # buy pays up 100 bps
        self.assertEqual(fill.price, 100.0)  # recorded reference price (SP 2.39)
        self.assertAlmostEqual(cost.slippage_cost, 10.0, places=2)
        self.assertEqual(cost.regulatory_fee, 0.0)  # buy: no SEC fee
        self.assertAlmostEqual(fill.fee, cost.total_cost, places=2)
        self.assertGreater(cost.total_cost, 0.0)  # slippage lands inside the fee

    def test_us_regulatory_fee_applies_to_sell_only(self) -> None:
        buy = us_order_cost(symbol="AAPL", side=_BUY, quantity=10.0, price=100.0)
        sell = us_order_cost(symbol="AAPL", side=_SELL, quantity=10.0, price=100.0)
        self.assertEqual(buy.regulatory_fee, 0.0)
        self.assertGreater(sell.regulatory_fee, 0.0)
        self.assertGreater(sell.total_cost, buy.total_cost)

    def test_us_fill_cash_moves_in_usd(self) -> None:
        fill = simulate_fill(
            order=_order(symbol="AAPL", market=Market.US, quantity=10.0, currency=USD),
            rule=FillRule.CLOSE,
            quote=_quote(market=Market.US, symbol="AAPL", close=100.0),
        )
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=USD,
            amount=10_000.0,
            base_rate=7.8,
        )
        ledger = apply_fill(ledger, fill=fill)
        self.assertAlmostEqual(ledger.balance(USD), 10_000.0 - 1_000.0 - fill.fee, places=2)
        self.assertAlmostEqual(ledger.fees(USD), fill.fee, places=2)
        # The base-currency ledger balance is untouched by a USD buy.
        self.assertEqual(ledger.balance(HKD), 0.0)


class FxLedgerTests(unittest.TestCase):
    """FX: explicit conversion, P&L booking and base-currency valuation (2.42, 2.45)."""

    def test_cross_currency_valuation_converts_at_fx(self) -> None:
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=USD,
            amount=10_000.0,
            base_rate=7.8,
        )
        fill = simulate_fill(
            order=_order(symbol="AAPL", market=Market.US, quantity=10.0, currency=USD),
            rule=FillRule.CLOSE,
            quote=_quote(market=Market.US, symbol="AAPL", close=100.0),
        )
        ledger = apply_fill(ledger, fill=fill)
        valuation = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[_position()],
            valuations={
                (Market.US, "AAPL"): PositionValuation(
                    market=Market.US,
                    symbol="AAPL",
                    price=100.0,
                    carried_forward=False,
                    day=_DAY,
                    warning=None,
                )
            },
            fx_rate=_fx(7.8),
        )
        position_value = valuation.position_values[0]
        self.assertEqual(position_value.currency, USD)
        self.assertEqual(position_value.fx_rate, 7.8)
        self.assertAlmostEqual(position_value.market_value_base, 7_800.0, places=2)
        self.assertAlmostEqual(
            valuation.net_value.total_value,
            8_999.5 * 7.8 + 7_800.0,
            places=2,
        )

    def test_missing_fx_refuses_valuation_not_1_to_1(self) -> None:
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=USD,
            amount=10_000.0,
            base_rate=7.8,
        )
        fill = simulate_fill(
            order=_order(symbol="AAPL", market=Market.US, quantity=10.0, currency=USD),
            rule=FillRule.CLOSE,
            quote=_quote(market=Market.US, symbol="AAPL", close=100.0),
        )
        ledger = apply_fill(ledger, fill=fill)
        with self.assertRaises(FxConversionError) as ctx:
            value_portfolio(
                as_of=_DAY,
                base_currency=HKD,
                ledger=ledger,
                positions=[_position()],
                valuations={
                    (Market.US, "AAPL"): PositionValuation(
                        market=Market.US,
                        symbol="AAPL",
                        price=100.0,
                        carried_forward=False,
                        day=_DAY,
                        warning=None,
                    )
                },
                fx_rate=_fx(None),
            )
        self.assertIn("refusing to assume 1:1", str(ctx.exception))

    def test_foreign_to_base_conversion_books_fx_pnl(self) -> None:
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=USD,
            amount=1_000.0,
            base_rate=7.0,
        )
        ledger = convert(
            ledger,
            from_currency=USD,
            to_currency=HKD,
            amount=500.0,
            rate=7.5,
        )
        self.assertAlmostEqual(ledger.balance(USD), 500.0, places=2)
        self.assertAlmostEqual(ledger.balance(HKD), 3_750.0, places=2)
        self.assertAlmostEqual(ledger.fx_pnl, 500.0 * 7.5 - 500.0 * 7.0, places=2)

    def test_round_trip_at_same_rate_books_no_pnl(self) -> None:
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=USD,
            amount=1_000.0,
            base_rate=7.0,
        )
        ledger = convert(
            ledger,
            from_currency=USD,
            to_currency=HKD,
            amount=1_000.0,
            rate=7.0,
        )
        self.assertEqual(ledger.fx_pnl, 0.0)

    def test_ledger_keeps_currencies_separate(self) -> None:
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=HKD,
            amount=100_000.0,
            base_rate=1.0,
        )
        ledger = deposit(ledger, currency=USD, amount=10_000.0, base_rate=7.8)
        hk_fill = simulate_fill(
            order=_order(quantity=800.0),
            rule=FillRule.CLOSE,
            quote=_quote(close=10.0),
        )
        us_fill = simulate_fill(
            order=_order(symbol="AAPL", market=Market.US, quantity=10.0, currency=USD),
            rule=FillRule.CLOSE,
            quote=_quote(market=Market.US, symbol="AAPL", close=100.0),
        )
        ledger = apply_fill(apply_fill(ledger, fill=hk_fill), fill=us_fill)
        self.assertEqual(set(ledger.currencies()), {HKD, USD})
        self.assertAlmostEqual(
            ledger.balance(HKD),
            100_000.0 - 8_000.0 - hk_fill.fee,
            places=2,
        )
        self.assertAlmostEqual(
            ledger.balance(USD),
            10_000.0 - 1_000.0 - us_fill.fee,
            places=2,
        )


class InsufficientCashTests(unittest.TestCase):
    """Cash shortfall: a buy beyond available cash is refused (2.42)."""

    def test_buy_beyond_cash_refused(self) -> None:
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=HKD,
            amount=1_000.0,
            base_rate=1.0,
        )
        fill = simulate_fill(
            order=_order(quantity=200.0),
            rule=FillRule.CLOSE,
            quote=_quote(close=10.0),
        )
        with self.assertRaises(InsufficientCashError) as ctx:
            apply_fill(ledger, fill=fill)
        self.assertIn("Insufficient HKD cash for BUY 0001.HK", str(ctx.exception))
        # The ledger is untouched by a refused buy.
        self.assertEqual(ledger.balance(HKD), 1_000.0)

    def test_sell_proceeds_fund_a_later_buy(self) -> None:
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=HKD,
            amount=100_000.0,
            base_rate=1.0,
        )
        buy = simulate_fill(
            order=_order(quantity=800.0),
            rule=FillRule.CLOSE,
            quote=_quote(close=10.0),
        )
        ledger = apply_fill(ledger, fill=buy)
        sell = simulate_fill(
            order=_order(side=_SELL, quantity=500.0),
            rule=FillRule.CLOSE,
            quote=_quote(close=12.0),
        )
        ledger = apply_fill(ledger, fill=sell)
        proceeds = 500.0 * 12.0 - sell.fee
        self.assertAlmostEqual(
            ledger.balance(HKD),
            100_000.0 - buy.notional - buy.fee + proceeds,
            places=2,
        )
        # The freed cash now covers a fresh buy.
        second = simulate_fill(
            order=_order(quantity=300.0),
            rule=FillRule.CLOSE,
            quote=_quote(close=10.0),
        )
        ledger = apply_fill(ledger, fill=second)
        self.assertAlmostEqual(
            ledger.balance(HKD),
            100_000.0 - buy.notional - buy.fee + proceeds - second.notional - second.fee,
            places=2,
        )

    def test_credit_requires_positive_amount(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        with self.assertRaises(ValueError):
            credit(ledger, currency=HKD, amount=0.0)


class SuspensionExecutionTests(unittest.TestCase):
    """Suspension: no new fills, positions carried at the last close (2.41)."""

    def test_order_refused_when_no_quote(self) -> None:
        refused = refuse_order(order=_order(), day=_DAY, quote=None)
        self.assertIsInstance(refused, RefusedOrder)
        self.assertIsNotNone(refused)
        self.assertIn("no quote on 2024-01-02", refused.reason)
        self.assertIn("suspended or untradeable", refused.reason)

    def test_order_refused_when_zero_volume(self) -> None:
        quote = _quote(volume=0)
        refused = refuse_order(order=_order(), day=_DAY, quote=quote)
        self.assertIsNotNone(refused)
        self.assertIn("zero volume on 2024-01-02", refused.reason)
        self.assertFalse(is_tradeable(quote))

    def test_position_carried_forward_with_warning(self) -> None:
        last = _quote(day=_LAST, close=9.5)
        valuation = position_valuation_price(
            market=Market.HK,
            symbol="0001.HK",
            day=_DAY,
            quote=None,
            last_quote=last,
        )
        self.assertTrue(valuation.carried_forward)
        self.assertEqual(valuation.price, 9.5)
        self.assertIsInstance(valuation.warning, SuspensionWarning)
        self.assertIn("last available close", valuation.warning.message)

    def test_position_valued_at_quoted_close_without_warning(self) -> None:
        quote = _quote(close=10.5)
        valuation = position_valuation_price(
            market=Market.HK,
            symbol="0001.HK",
            day=_DAY,
            quote=quote,
            last_quote=None,
        )
        self.assertFalse(valuation.carried_forward)
        self.assertEqual(valuation.price, 10.5)
        self.assertIsNone(valuation.warning)

    def test_no_price_available_raises(self) -> None:
        with self.assertRaises(ValueError):
            position_valuation_price(
                market=Market.HK,
                symbol="0001.HK",
                day=_DAY,
                quote=None,
                last_quote=None,
            )

    def test_carried_forward_valuation_reaches_the_portfolio(self) -> None:
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=HKD,
            amount=100_000.0,
            base_rate=1.0,
        )
        valuation = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[_position(symbol="0001.HK", market=Market.HK, currency=HKD)],
            valuations={
                (Market.HK, "0001.HK"): PositionValuation(
                    market=Market.HK,
                    symbol="0001.HK",
                    price=9.5,
                    carried_forward=True,
                    day=_DAY,
                    warning=SuspensionWarning(
                        market=Market.HK,
                        symbol="0001.HK",
                        day=_DAY,
                        message="stale",
                    ),
                )
            },
            fx_rate=_fx(1.0),
        )
        pv = valuation.position_values[0]
        self.assertTrue(pv.carried_forward)
        self.assertIsNotNone(pv.warning)


class PartialFillTests(unittest.TestCase):
    """Partial fills: participation-rate capping and cancel/defer policy (2.40)."""

    def test_partial_fill_capped_by_participation_rate(self) -> None:
        outcome = apply_volume_limit(
            order=_order(quantity=1_000.0),
            reference_price=10.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        self.assertEqual(outcome.max_fill_quantity, 500.0)
        self.assertEqual(outcome.filled_quantity, 500.0)
        self.assertTrue(outcome.is_partial)
        self.assertIn("partially filled", outcome.reason)

    def test_cancel_and_defer_policies(self) -> None:
        cancel = apply_volume_limit(
            order=_order(quantity=1_000.0),
            reference_price=10.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        defer = apply_volume_limit(
            order=_order(quantity=1_000.0),
            reference_price=10.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.DEFER,
        )
        self.assertEqual(cancel.cancelled_quantity, 500.0)
        self.assertEqual(cancel.deferred_quantity, 0.0)
        self.assertEqual(defer.deferred_quantity, 500.0)
        self.assertEqual(defer.cancelled_quantity, 0.0)

    def test_full_fill_within_the_limit(self) -> None:
        outcome = apply_volume_limit(
            order=_order(quantity=100.0),
            reference_price=10.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        self.assertTrue(outcome.is_full)
        self.assertEqual(outcome.filled_quantity, 100.0)
        self.assertIn("fully filled", outcome.reason)

    def test_unfilled_when_the_cap_is_zero(self) -> None:
        outcome = apply_volume_limit(
            order=_order(quantity=1_000.0),
            reference_price=10.0,
            volume=0,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        self.assertTrue(outcome.is_unfilled)
        self.assertEqual(outcome.filled_quantity, 0.0)
        self.assertIn("unfilled", outcome.reason)
        self.assertIn("at most 0.00", outcome.reason)

    def test_partial_fill_applies_only_the_filled_quantity(self) -> None:
        outcome = apply_volume_limit(
            order=_order(quantity=1_000.0),
            reference_price=10.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        filled_order = _order(quantity=outcome.filled_quantity)
        fill = simulate_fill(
            order=filled_order,
            rule=FillRule.CLOSE,
            quote=_quote(close=10.0),
        )
        ledger = deposit(
            empty_ledger(as_of=_DAY, base_currency=HKD),
            currency=HKD,
            amount=100_000.0,
            base_rate=1.0,
        )
        ledger = apply_fill(ledger, fill=fill)
        expected_fee = hk_order_cost(
            symbol="0001.HK", side=_BUY, quantity=500.0, price=10.0
        ).total_fee
        self.assertAlmostEqual(
            ledger.balance(HKD),
            100_000.0 - 5_000.0 - expected_fee,
            places=2,
        )


class BoardLotAndFractionalTests(unittest.TestCase):
    """Board lots (HK) vs fractional shares (US): 手数/碎股 (2.37, 2.38)."""

    def test_hk_rounds_down_to_whole_lots(self) -> None:
        self.assertEqual(round_to_lot(250.0, 100), 200.0)
        self.assertEqual(round_to_lot(99.0, 100), 0.0)
        self.assertEqual(round_to_lot(1_000.0, 100), 1_000.0)

    def test_hk_lot_buy_stays_within_intended_notional(self) -> None:
        quantity = 250.0
        rounded = round_to_lot(quantity, 100)
        self.assertEqual(rounded, 200.0)
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=rounded, price=10.0)
        self.assertLessEqual(cost.notional, quantity * 10.0)

    def test_us_fractional_shares_are_kept(self) -> None:
        self.assertEqual(round_to_fraction(100.5), 100.5)
        self.assertEqual(round_to_fraction(0.5), 0.5)
        fill = simulate_fill(
            order=_order(symbol="AAPL", market=Market.US, quantity=100.5, currency=USD),
            rule=FillRule.CLOSE,
            quote=_quote(market=Market.US, symbol="AAPL", close=100.0),
        )
        self.assertEqual(fill.quantity, 100.5)

    def test_hk_sell_never_exceeds_intended_quantity(self) -> None:
        quantity = 250.0
        rounded = round_to_lot(quantity, 100)
        self.assertLessEqual(rounded, quantity)
        cost = hk_order_cost(symbol="0001.HK", side=_SELL, quantity=rounded, price=10.0)
        self.assertEqual(cost.quantity, 200.0)


if __name__ == "__main__":
    unittest.main()
