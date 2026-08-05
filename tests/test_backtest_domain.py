"""Backtest domain type tests (MVP 2 / SP 2.2)."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import date

from harbor.config import MarketTarget
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
    OrderType,
    Position,
    TradingDay,
    from_market_target,
    to_market_target,
)


class MarketConversionTests(unittest.TestCase):
    """Verify the backtest market vocabulary and its bridge to MarketTarget."""

    def test_market_covers_only_concrete_markets(self) -> None:
        self.assertEqual(set(Market), {MarketTarget.HK, MarketTarget.US})

    def test_round_trip_with_market_target(self) -> None:
        for market in Market:
            self.assertEqual(from_market_target(to_market_target(market)), market)

    def test_from_market_target_rejects_both(self) -> None:
        with self.assertRaisesRegex(ValueError, "single concrete market"):
            from_market_target(MarketTarget.BOTH)


class CurrencyTests(unittest.TestCase):
    """Verify the currency vocabulary used by the backtest ledger."""

    def test_currency_values(self) -> None:
        self.assertEqual(Currency.HKD.value, "HKD")
        self.assertEqual(Currency.USD.value, "USD")

    def test_hk_market_quotes_in_hkd(self) -> None:
        self.assertEqual(Market.HK, Market("HK"))


class TradingDayTests(unittest.TestCase):
    """Verify the trading-day value type."""

    def test_defaults_to_trading_day(self) -> None:
        day = TradingDay(market=Market.HK, date=date(2026, 1, 2))
        self.assertTrue(day.is_trading_day)
        self.assertFalse(day.is_rebalance_day)

    def test_rebalance_day_must_be_trading_day(self) -> None:
        with self.assertRaisesRegex(ValueError, "rebalance day must be a trading day"):
            TradingDay(
                market=Market.US,
                date=date(2026, 1, 2),
                is_trading_day=False,
                is_rebalance_day=True,
            )

    def test_days_are_scoped_to_a_market(self) -> None:
        hk = TradingDay(market=Market.HK, date=date(2026, 1, 2))
        us = TradingDay(market=Market.US, date=date(2026, 1, 2))
        self.assertNotEqual(hk, us)


class OrderTests(unittest.TestCase):
    """Verify order validation and immutability."""

    def test_valid_market_order(self) -> None:
        order = Order(
            symbol="0005.HK",
            market=Market.HK,
            side=OrderSide.BUY,
            quantity=500.0,
            currency=Currency.HKD,
            trade_date=date(2026, 1, 2),
            ref="rebalance-2026Q1",
        )
        self.assertEqual(order.order_type, OrderType.MARKET)
        self.assertIsNone(order.limit_price)
        self.assertEqual(order.ref, "rebalance-2026Q1")

    def test_rejects_empty_symbol(self) -> None:
        with self.assertRaisesRegex(ValueError, "symbol must be non-empty"):
            Order(
                symbol="",
                market=Market.US,
                side=OrderSide.BUY,
                quantity=1.0,
                currency=Currency.USD,
                trade_date=date(2026, 1, 2),
            )

    def test_rejects_non_positive_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "quantity must be positive"):
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                quantity=0.0,
                currency=Currency.USD,
                trade_date=date(2026, 1, 2),
            )

    def test_limit_order_requires_limit_price(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit order requires a limit price"):
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                quantity=1.0,
                currency=Currency.USD,
                trade_date=date(2026, 1, 2),
                order_type=OrderType.LIMIT,
            )

    def test_market_order_cannot_carry_limit_price(self) -> None:
        with self.assertRaisesRegex(ValueError, "market order cannot carry a limit price"):
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                quantity=1.0,
                currency=Currency.USD,
                trade_date=date(2026, 1, 2),
                limit_price=100.0,
            )

    def test_is_immutable(self) -> None:
        order = Order(
            symbol="AAPL",
            market=Market.US,
            side=OrderSide.BUY,
            quantity=1.0,
            currency=Currency.USD,
            trade_date=date(2026, 1, 2),
        )
        with self.assertRaises(FrozenInstanceError):
            order.quantity = 2.0  # type: ignore[misc]


class FillTests(unittest.TestCase):
    """Verify the executed-order value type."""

    def test_notional_is_quantity_times_price(self) -> None:
        fill = Fill(
            order_ref="o-1",
            symbol="0005.HK",
            market=Market.HK,
            side=OrderSide.BUY,
            quantity=500.0,
            price=60.0,
            currency=Currency.HKD,
            trade_date=date(2026, 1, 2),
            fee=10.0,
        )
        self.assertEqual(fill.notional, 30_000.0)
        self.assertEqual(fill.fee, 10.0)

    def test_rejects_negative_price(self) -> None:
        with self.assertRaisesRegex(ValueError, "price must be non-negative"):
            Fill(
                order_ref="o-1",
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.SELL,
                quantity=1.0,
                price=-1.0,
                currency=Currency.USD,
                trade_date=date(2026, 1, 2),
            )

    def test_rejects_negative_fee(self) -> None:
        with self.assertRaisesRegex(ValueError, "fee must be non-negative"):
            Fill(
                order_ref="o-1",
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.SELL,
                quantity=1.0,
                price=100.0,
                currency=Currency.USD,
                trade_date=date(2026, 1, 2),
                fee=-1.0,
            )


class PositionTests(unittest.TestCase):
    """Verify the position value type."""

    def test_cost_basis_is_quantity_times_average_cost(self) -> None:
        position = Position(
            symbol="AAPL",
            market=Market.US,
            quantity=10.0,
            average_cost=150.0,
            currency=Currency.USD,
            as_of_date=date(2026, 1, 2),
        )
        self.assertEqual(position.cost_basis, 1_500.0)

    def test_rejects_negative_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "quantity must be non-negative"):
            Position(
                symbol="AAPL",
                market=Market.US,
                quantity=-1.0,
                average_cost=150.0,
                currency=Currency.USD,
                as_of_date=date(2026, 1, 2),
            )

    def test_rejects_negative_average_cost(self) -> None:
        with self.assertRaisesRegex(ValueError, "average cost must be non-negative"):
            Position(
                symbol="AAPL",
                market=Market.US,
                quantity=1.0,
                average_cost=-1.0,
                currency=Currency.USD,
                as_of_date=date(2026, 1, 2),
            )


class CashBalanceTests(unittest.TestCase):
    """Verify the cash balance value type."""

    def test_valid_balance(self) -> None:
        balance = CashBalance(currency=Currency.HKD, amount=100_000.0)
        self.assertEqual(balance.amount, 100_000.0)

    def test_rejects_negative_balance(self) -> None:
        with self.assertRaisesRegex(ValueError, "Cash amount must be non-negative"):
            CashBalance(currency=Currency.HKD, amount=-1.0)


class NetValueTests(unittest.TestCase):
    """Verify the net-value snapshot type."""

    def test_total_value_sums_cash_and_securities(self) -> None:
        snapshot = NetValue(
            as_of_date=date(2026, 1, 2),
            currency=Currency.HKD,
            cash=50_000.0,
            securities_value=150_000.0,
            fees_paid=120.0,
        )
        self.assertEqual(snapshot.total_value, 200_000.0)

    def test_rejects_negative_securities_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "securities must be non-negative"):
            NetValue(
                as_of_date=date(2026, 1, 2),
                currency=Currency.USD,
                cash=0.0,
                securities_value=-1.0,
            )


class BacktestStateTests(unittest.TestCase):
    """Verify the backtest state snapshot type."""

    def test_initializing_state_has_empty_portfolio(self) -> None:
        state = BacktestState(
            status=BacktestStatus.INITIALIZING,
            as_of_date=date(2026, 1, 2),
        )
        self.assertEqual(state.positions, ())
        self.assertEqual(state.cash, ())
        self.assertIsNone(state.next_rebalance_date)

    def test_failed_state_requires_error_message(self) -> None:
        with self.assertRaisesRegex(ValueError, "failed backtest must carry an error message"):
            BacktestState(
                status=BacktestStatus.FAILED,
                as_of_date=date(2026, 1, 2),
            )

    def test_completed_state_with_diagnostics(self) -> None:
        position = Position(
            symbol="AAPL",
            market=Market.US,
            quantity=10.0,
            average_cost=150.0,
            currency=Currency.USD,
            as_of_date=date(2026, 12, 31),
        )
        state = BacktestState(
            status=BacktestStatus.COMPLETED,
            as_of_date=date(2026, 12, 31),
            positions=(position,),
            cash=(CashBalance(currency=Currency.USD, amount=5_000.0),),
        )
        self.assertEqual(len(state.positions), 1)
        self.assertEqual(state.cash[0].amount, 5_000.0)

    def test_failed_state_can_carry_error_message(self) -> None:
        state = BacktestState(
            status=BacktestStatus.FAILED,
            as_of_date=date(2026, 1, 2),
            error_message="precheck failed: missing FX data",
        )
        self.assertIn("missing FX data", state.error_message or "")


class ImmutabilityAndHashingTests(unittest.TestCase):
    """Frozen values must be usable as set members for deterministic replay."""

    def test_equal_values_hash_equal(self) -> None:
        first = TradingDay(market=Market.HK, date=date(2026, 1, 2))
        second = TradingDay(market=Market.HK, date=date(2026, 1, 2))
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        self.assertEqual(len({first, second}), 1)

    def test_position_can_be_set_member(self) -> None:
        position = Position(
            symbol="AAPL",
            market=Market.US,
            quantity=10.0,
            average_cost=150.0,
            currency=Currency.USD,
            as_of_date=date(2026, 1, 2),
        )
        self.assertIn(position, {position})


if __name__ == "__main__":
    unittest.main()
