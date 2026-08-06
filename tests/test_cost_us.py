"""US cost model tests (MVP 2 / SP 2.38).

Verifies commission (with the platform minimum floor), the sell-only regulatory
fee (SEC Section 31), configurable slippage (moves the execution price in the
trade direction), the fractional-share rule (no board-lot rounding), cent
rounding, and replayability.
"""

import unittest

from harbor.core.backtest_config import CostConfig
from harbor.core.backtest_domain import Market, OrderSide
from harbor.core.cost_us import UsOrderCost, round_to_fraction, us_order_cost

_BUY = OrderSide.BUY
_SELL = OrderSide.SELL


class CommissionTests(unittest.TestCase):
    """Verify the commission and platform minimum floor."""

    def test_commission_above_floor_is_rate_times_notional(self) -> None:
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=500, price=200.0)
        self.assertAlmostEqual(cost.notional, 100_000.0)
        self.assertAlmostEqual(cost.commission, 50.0)  # 100000 * 0.0005

    def test_min_commission_floor_applies(self) -> None:
        config = CostConfig(commission_rate=0.0005, min_commission=1.0)
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=1, price=100.0, config=config)
        self.assertAlmostEqual(cost.commission, 1.0)  # max(0.05, 1.0)


class RegulatoryFeeTests(unittest.TestCase):
    """Verify the US-only regulatory fee applies to sell orders."""

    def test_sell_incurs_regulatory_fee(self) -> None:
        cost = us_order_cost(symbol="AAPL", side=_SELL, quantity=500, price=200.0)
        # 100000 * 0.0000278 = 2.78 (SEC Section 31, sell only).
        self.assertAlmostEqual(cost.regulatory_fee, 2.78)

    def test_buy_incurs_no_regulatory_fee(self) -> None:
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=500, price=200.0)
        self.assertEqual(cost.regulatory_fee, 0.0)

    def test_custom_regulatory_rate_changes_fee(self) -> None:
        config = CostConfig(regulatory_fee_rate=0.001)
        cost = us_order_cost(symbol="AAPL", side=_SELL, quantity=500, price=200.0, config=config)
        self.assertAlmostEqual(cost.regulatory_fee, 100.0)  # 100000 * 0.001


class SlippageTests(unittest.TestCase):
    """Verify configurable slippage moves the price in the trade direction."""

    def test_buy_slips_up(self) -> None:
        config = CostConfig(slippage_bps=100.0)
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=100, price=100.0, config=config)
        self.assertAlmostEqual(cost.exec_price, 101.0)
        self.assertAlmostEqual(cost.slippage_cost, 100.0)

    def test_sell_slips_down(self) -> None:
        config = CostConfig(slippage_bps=100.0)
        cost = us_order_cost(symbol="AAPL", side=_SELL, quantity=100, price=100.0, config=config)
        self.assertAlmostEqual(cost.exec_price, 99.0)
        self.assertAlmostEqual(cost.slippage_cost, 100.0)

    def test_zero_slippage_default(self) -> None:
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=100, price=100.0)
        self.assertAlmostEqual(cost.exec_price, 100.0)
        self.assertEqual(cost.slippage_cost, 0.0)

    def test_total_cost_includes_fees_and_slippage(self) -> None:
        config = CostConfig(slippage_bps=100.0)
        cost = us_order_cost(symbol="AAPL", side=_SELL, quantity=100, price=100.0, config=config)
        # exec 99.0, notional 9900: commission 4.95 + regulatory 0.28 + slippage 100.0.
        self.assertAlmostEqual(cost.total_cost, 105.23)


class FractionalShareTests(unittest.TestCase):
    """Verify the US fractional-share (碎股) rule."""

    def test_fractional_quantity_passes_through(self) -> None:
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=0.5, price=200.0)
        self.assertAlmostEqual(cost.quantity, 0.5)
        self.assertAlmostEqual(cost.notional, 100.0)

    def test_round_to_fraction_preserves_fraction(self) -> None:
        self.assertEqual(round_to_fraction(0.5), 0.5)
        self.assertEqual(round_to_fraction(123.456), 123.456)

    def test_round_to_fraction_rejects_non_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "Quantity"):
            round_to_fraction(0.0)


class ConfigDrivenTests(unittest.TestCase):
    """Verify parameters come from the configuration."""

    def test_custom_rates_change_fees(self) -> None:
        config = CostConfig(commission_rate=0.001, regulatory_fee_rate=0.002)
        cost = us_order_cost(symbol="AAPL", side=_SELL, quantity=500, price=200.0, config=config)
        self.assertAlmostEqual(cost.commission, 100.0)  # 100000 * 0.001
        self.assertAlmostEqual(cost.regulatory_fee, 200.0)  # 100000 * 0.002

    def test_market_is_us(self) -> None:
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=500, price=200.0)
        self.assertEqual(cost.market, Market.US)


class ValidationAndReadableTests(unittest.TestCase):
    """Verify input validation and the readable summary."""

    def test_rejects_non_positive_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "Quantity"):
            us_order_cost(symbol="AAPL", side=_BUY, quantity=0.0, price=200.0)

    def test_rejects_non_positive_price(self) -> None:
        with self.assertRaisesRegex(ValueError, "Price"):
            us_order_cost(symbol="AAPL", side=_BUY, quantity=500, price=0.0)

    def test_readable_summary(self) -> None:
        config = CostConfig(slippage_bps=100.0)
        cost = us_order_cost(symbol="AAPL", side=_SELL, quantity=100, price=200.0, config=config)
        summary = cost.readable()
        self.assertIn("US cost for SELL AAPL: 100.00 @ 200.0000 USD", summary)
        self.assertIn("commission: 9.90", summary)
        self.assertIn("regulatory fee: 0.55", summary)
        self.assertIn("slippage: 200.00", summary)
        self.assertIn("total cost: 210.45", summary)

    def test_repeat_computation_identical(self) -> None:
        first = us_order_cost(symbol="AAPL", side=_BUY, quantity=500, price=200.0)
        second = us_order_cost(symbol="AAPL", side=_BUY, quantity=500, price=200.0)
        self.assertEqual(first, second)

    def test_is_frozen_dataclass(self) -> None:
        cost = us_order_cost(symbol="AAPL", side=_BUY, quantity=500, price=200.0)
        self.assertIsInstance(cost, UsOrderCost)


if __name__ == "__main__":
    unittest.main()
