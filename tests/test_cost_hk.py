"""Hong Kong cost model tests (MVP 2 / SP 2.37).

Verifies commission (with the platform minimum floor), stamp duty, transaction
levy, trading fee, cent rounding, the board-lot (手数) rule, that the US-only
regulatory fee is not applied, and replayability.
"""

import unittest

from harbor.core.backtest_config import CostConfig
from harbor.core.backtest_domain import Market, OrderSide
from harbor.core.cost_hk import HkOrderCost, hk_order_cost, round_to_lot

_BUY = OrderSide.BUY
_SELL = OrderSide.SELL


class CommissionTests(unittest.TestCase):
    """Verify the commission and platform minimum floor."""

    def test_commission_above_floor_is_rate_times_notional(self) -> None:
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800, price=50.0)
        self.assertAlmostEqual(cost.notional, 40_000.0)
        self.assertAlmostEqual(cost.commission, 20.0)  # 40000 * 0.0005

    def test_min_commission_floor_applies(self) -> None:
        config = CostConfig(commission_rate=0.0005, min_commission=5.0)
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=100, price=1.0, config=config)
        self.assertAlmostEqual(cost.commission, 5.0)  # max(0.05, 5.0)


class ProportionalFeeTests(unittest.TestCase):
    """Verify stamp duty, transaction levy and trading fee."""

    def test_fee_components_and_total(self) -> None:
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800, price=50.0)
        self.assertAlmostEqual(cost.stamp_duty, 40.0)  # 40000 * 0.001
        self.assertAlmostEqual(cost.transaction_levy, 1.08)  # 40000 * 0.000027
        self.assertAlmostEqual(cost.trading_fee, 2.26)  # 40000 * 0.0000565
        self.assertAlmostEqual(cost.total_fee, 63.34)

    def test_fees_rounded_to_cents(self) -> None:
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=1, price=33333.0)
        for fee in (
            cost.commission,
            cost.stamp_duty,
            cost.transaction_levy,
            cost.trading_fee,
            cost.total_fee,
        ):
            self.assertEqual(round(fee, 2), fee)

    def test_buy_and_sell_incur_same_hk_fees(self) -> None:
        buy = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800, price=50.0)
        sell = hk_order_cost(symbol="0001.HK", side=_SELL, quantity=800, price=50.0)
        self.assertEqual(buy.total_fee, sell.total_fee)


class ConfigDrivenTests(unittest.TestCase):
    """Verify parameters come from the configuration."""

    def test_custom_rates_change_fees(self) -> None:
        config = CostConfig(
            commission_rate=0.001,
            stamp_duty_rate=0.002,
            transaction_levy_rate=0.0,
            trading_fee_rate=0.0,
        )
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=1_000, price=50.0, config=config)
        self.assertAlmostEqual(cost.commission, 50.0)  # 50000 * 0.001
        self.assertAlmostEqual(cost.stamp_duty, 100.0)  # 50000 * 0.002

    def test_us_regulatory_fee_not_applied(self) -> None:
        config = CostConfig(regulatory_fee_rate=0.5)
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800, price=50.0, config=config)
        # The US-only regulatory fee must not leak into the HK total.
        self.assertAlmostEqual(cost.total_fee, 63.34)


class LotRuleTests(unittest.TestCase):
    """Verify the board-lot (手数) rule."""

    def test_rounds_down_to_whole_lots(self) -> None:
        self.assertEqual(round_to_lot(123.0, 100), 100.0)
        self.assertEqual(round_to_lot(99.0, 100), 0.0)
        self.assertEqual(round_to_lot(250.0, 100), 200.0)
        self.assertEqual(round_to_lot(200.0, 100), 200.0)

    def test_custom_lot_size(self) -> None:
        self.assertEqual(round_to_lot(123.0, 50), 100.0)

    def test_rejects_non_positive_lot_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "lot_size"):
            round_to_lot(100.0, 0)

    def test_rejects_negative_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "quantity"):
            round_to_lot(-1.0, 100)


class ValidationAndReadableTests(unittest.TestCase):
    """Verify input validation and the readable summary."""

    def test_rejects_non_positive_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "Quantity"):
            hk_order_cost(symbol="0001.HK", side=_BUY, quantity=0.0, price=50.0)

    def test_rejects_non_positive_price(self) -> None:
        with self.assertRaisesRegex(ValueError, "Price"):
            hk_order_cost(symbol="0001.HK", side=_BUY, quantity=100.0, price=0.0)

    def test_readable_summary(self) -> None:
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800, price=50.0)
        summary = cost.readable()
        self.assertIn("HK cost for BUY 0001.HK: 800.00 @ 50.0000 HKD", summary)
        self.assertIn("commission: 20.00", summary)
        self.assertIn("stamp duty: 40.00", summary)
        self.assertIn("total fee: 63.34", summary)

    def test_repeat_computation_identical(self) -> None:
        first = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800, price=50.0)
        second = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800, price=50.0)
        self.assertEqual(first, second)

    def test_is_frozen_dataclass(self) -> None:
        cost = hk_order_cost(symbol="0001.HK", side=_BUY, quantity=800, price=50.0)
        self.assertIsInstance(cost, HkOrderCost)
        self.assertEqual(cost.market, Market.HK)


if __name__ == "__main__":
    unittest.main()
