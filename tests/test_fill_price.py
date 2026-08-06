"""Fill price & slippage tests (MVP 2 / SP 2.39).

Verifies the open / close / next-open fill rules, the direction-aware slippage
helper (including its consistency with the US cost model), and that
simulate_fill builds auditable fills with the market-appropriate trading cost
(HK fees only; US fees + slippage).
"""

import unittest
from datetime import date

from harbor.core.backtest_config import CostConfig, FillRule
from harbor.core.backtest_domain import Currency, Fill, Market, Order, OrderSide
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.cost_us import us_order_cost
from harbor.core.fill_price import apply_slippage, resolve_fill_price, simulate_fill

_BUY = OrderSide.BUY
_SELL = OrderSide.SELL

_DAY = date(2024, 1, 2)
_NEXT = date(2024, 1, 3)


def _quote(
    *,
    day: date = _DAY,
    open: float = 10.0,
    high: float = 12.0,
    low: float = 9.5,
    close: float = 11.0,
    volume: int = 1_000,
    market: Market = Market.HK,
    symbol: str = "0001.HK",
) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=open,
        high=high,
        low=low,
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
    currency: Currency = Currency.HKD,
    trade_date: date = _DAY,
    ref: str = "rebalance-1",
) -> Order:
    return Order(
        symbol=symbol,
        market=market,
        side=side,
        quantity=quantity,
        currency=currency,
        trade_date=trade_date,
        ref=ref,
    )


class ResolveFillPriceTests(unittest.TestCase):
    """Verify the open / close / next-open price rules."""

    def test_open_rule_uses_open(self) -> None:
        quote = _quote(open=10.0, close=11.0)
        self.assertEqual(resolve_fill_price(rule=FillRule.OPEN, quote=quote), 10.0)

    def test_close_rule_uses_close(self) -> None:
        quote = _quote(open=10.0, close=11.0)
        self.assertEqual(resolve_fill_price(rule=FillRule.CLOSE, quote=quote), 11.0)

    def test_next_open_rule_uses_next_open(self) -> None:
        nxt = _quote(day=_NEXT, open=12.5)
        price = resolve_fill_price(rule=FillRule.NEXT_OPEN, quote=_quote(), next_quote=nxt)
        self.assertEqual(price, 12.5)

    def test_next_open_requires_next_quote(self) -> None:
        with self.assertRaisesRegex(ValueError, "next trading day"):
            resolve_fill_price(rule=FillRule.NEXT_OPEN, quote=_quote())

    def test_resolution_is_deterministic(self) -> None:
        quote = _quote(close=11.0)
        self.assertEqual(
            resolve_fill_price(rule=FillRule.CLOSE, quote=quote),
            resolve_fill_price(rule=FillRule.CLOSE, quote=quote),
        )


class ApplySlippageTests(unittest.TestCase):
    """Verify the direction-aware slippage adjustment."""

    def test_buy_slips_up(self) -> None:
        self.assertAlmostEqual(apply_slippage(price=100.0, side=_BUY, slippage_bps=100.0), 101.0)

    def test_sell_slips_down(self) -> None:
        self.assertAlmostEqual(apply_slippage(price=100.0, side=_SELL, slippage_bps=100.0), 99.0)

    def test_zero_slippage_is_identity(self) -> None:
        self.assertEqual(apply_slippage(price=100.0, side=_BUY, slippage_bps=0.0), 100.0)

    def test_matches_us_cost_model(self) -> None:
        config = CostConfig(slippage_bps=100.0)
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=100, price=100.0, config=config)
        adjusted = apply_slippage(price=100.0, side=_BUY, slippage_bps=100.0)
        self.assertAlmostEqual(cost.exec_price, adjusted)

    def test_rejects_non_positive_price(self) -> None:
        with self.assertRaisesRegex(ValueError, "Price"):
            apply_slippage(price=0.0, side=_BUY, slippage_bps=100.0)

    def test_rejects_negative_slippage(self) -> None:
        with self.assertRaisesRegex(ValueError, "slippage_bps"):
            apply_slippage(price=100.0, side=_BUY, slippage_bps=-1.0)


class SimulateFillTests(unittest.TestCase):
    """Verify simulate_fill builds auditable fills with market costs."""

    def test_close_fill_price_and_hk_fee(self) -> None:
        quote = _quote(open=10.0, close=11.0)
        order = _order(side=_BUY, quantity=800.0)
        fill = simulate_fill(order=order, rule=FillRule.CLOSE, quote=quote)
        self.assertIsInstance(fill, Fill)
        self.assertAlmostEqual(fill.price, 11.0)
        # HK fees on notional 8800: commission 4.4, stamp 8.8, levy 0.24, trading 0.5.
        self.assertAlmostEqual(fill.fee, 13.94)

    def test_open_fill_price(self) -> None:
        quote = _quote(open=10.0, close=11.0)
        fill = simulate_fill(order=_order(), rule=FillRule.OPEN, quote=quote)
        self.assertAlmostEqual(fill.price, 10.0)

    def test_next_open_fill_price_and_date(self) -> None:
        nxt = _quote(day=_NEXT, open=12.5)
        fill = simulate_fill(
            order=_order(), rule=FillRule.NEXT_OPEN, quote=_quote(), next_quote=nxt
        )
        self.assertAlmostEqual(fill.price, 12.5)
        self.assertEqual(fill.trade_date, _NEXT)

    def test_next_open_requires_next_quote(self) -> None:
        with self.assertRaisesRegex(ValueError, "next trading day"):
            simulate_fill(order=_order(), rule=FillRule.NEXT_OPEN, quote=_quote())

    def test_carries_order_metadata(self) -> None:
        quote = _quote()
        order = _order(ref="rebalance-7")
        fill = simulate_fill(order=order, rule=FillRule.CLOSE, quote=quote)
        self.assertEqual(fill.order_ref, "rebalance-7")
        self.assertEqual(fill.symbol, order.symbol)
        self.assertEqual(fill.market, Market.HK)
        self.assertEqual(fill.side, _BUY)
        self.assertEqual(fill.quantity, 800.0)
        self.assertEqual(fill.currency, Currency.HKD)
        self.assertEqual(fill.trade_date, _DAY)

    def test_us_fill_fee_includes_slippage(self) -> None:
        config = CostConfig(slippage_bps=100.0)
        quote = _quote(market=Market.US, symbol="AAPL", open=100.0, close=100.0)
        order = _order(
            symbol="AAPL",
            market=Market.US,
            side=_SELL,
            quantity=100.0,
            currency=Currency.USD,
        )
        fill = simulate_fill(order=order, rule=FillRule.CLOSE, quote=quote, config=config)
        self.assertAlmostEqual(fill.price, 100.0)  # reference price per the rule
        self.assertAlmostEqual(fill.fee, 105.23)  # commission + regulatory + slippage

    def test_hk_fill_fee_ignores_slippage(self) -> None:
        config = CostConfig(slippage_bps=100.0)
        quote = _quote(open=10.0, close=11.0)
        fill = simulate_fill(
            order=_order(side=_BUY, quantity=800.0),
            rule=FillRule.CLOSE,
            quote=quote,
            config=config,
        )
        self.assertAlmostEqual(fill.fee, 13.94)  # HK cost model has no slippage

    def test_repeat_simulation_identical(self) -> None:
        quote = _quote(close=11.0)
        first = simulate_fill(order=_order(), rule=FillRule.CLOSE, quote=quote)
        second = simulate_fill(order=_order(), rule=FillRule.CLOSE, quote=quote)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
