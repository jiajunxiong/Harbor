"""Hong Kong paper order rule tests (MVP 4 / SP 4.17 / 4.28).

Verifies HK board-lot alignment (手数), below-one-lot rejection (不足一手),
price-step alignment (板位) and minimum-quantity boundaries — parameters from
the configuration.
"""

import unittest

import pydantic

from harbor.core.backtest_domain import Market
from harbor.core.paper_order_rules import (
    HkOrderRuleConfig,
    OrderRuleStatus,
    align_hk_price,
    apply_hk_order_rule,
    apply_paper_order_rule,
)


class ApplyHkOrderRuleTests(unittest.TestCase):
    """HK board-lot alignment (SP 4.17 / 4.28)."""

    def test_exact_lot_ok(self) -> None:
        outcome = apply_hk_order_rule(100.0)
        self.assertEqual(outcome.status, OrderRuleStatus.OK)
        self.assertEqual(outcome.quantity, 100.0)

    def test_rounds_down_to_whole_lots(self) -> None:
        outcome = apply_hk_order_rule(250.0)
        self.assertEqual(outcome.status, OrderRuleStatus.ADJUSTED)
        self.assertEqual(outcome.quantity, 200.0)
        self.assertIn("rounded down to 2 whole lot(s)", outcome.reason or "")

    def test_large_quantity_rounds_down(self) -> None:
        outcome = apply_hk_order_rule(1_234.0)
        self.assertEqual(outcome.quantity, 1_200.0)

    def test_below_one_lot_rejected(self) -> None:
        outcome = apply_hk_order_rule(99.0)
        self.assertEqual(outcome.status, OrderRuleStatus.REJECTED)
        self.assertEqual(outcome.quantity, 0.0)
        self.assertFalse(outcome.ok)
        self.assertIn("below one lot", outcome.reason or "")
        self.assertIn("不足一手", outcome.reason or "")

    def test_odd_lot_allowed_by_config(self) -> None:
        outcome = apply_hk_order_rule(50.0, config=HkOrderRuleConfig(allow_odd_lot=True))
        self.assertEqual(outcome.status, OrderRuleStatus.ADJUSTED)
        self.assertEqual(outcome.quantity, 50.0)

    def test_custom_lot_size(self) -> None:
        outcome = apply_hk_order_rule(550.0, config=HkOrderRuleConfig(lot_size=200))
        self.assertEqual(outcome.quantity, 400.0)

    def test_non_positive_rejected(self) -> None:
        with self.assertRaises(ValueError):
            apply_hk_order_rule(0.0)

    def test_config_validated(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            HkOrderRuleConfig(lot_size=0)


class AlignHkPriceTests(unittest.TestCase):
    """HK price-step alignment (板位, SP 4.17 / 4.28)."""

    def test_aligns_to_cents(self) -> None:
        self.assertEqual(align_hk_price(49.966), 49.97)

    def test_aligns_to_custom_step(self) -> None:
        outcome = align_hk_price(49.3, config=HkOrderRuleConfig(price_step=0.5))
        self.assertEqual(outcome, 49.5)

    def test_exact_price_unchanged(self) -> None:
        self.assertEqual(align_hk_price(50.0), 50.0)


class DispatchTests(unittest.TestCase):
    """The per-market dispatch never mixes rules (SP 4.17 / 4.18)."""

    def test_dispatch_hk(self) -> None:
        outcome = apply_paper_order_rule(Market.HK, 250.0)
        self.assertEqual(outcome.quantity, 200.0)  # HK lots

    def test_dispatch_us(self) -> None:
        from harbor.core.paper_order_rules import apply_us_order_rule

        us_outcome = apply_us_order_rule(250.5)
        outcome = apply_paper_order_rule(Market.US, 250.5)
        self.assertEqual(outcome.quantity, us_outcome.quantity)
