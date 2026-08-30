"""United States paper order rule tests (MVP 4 / SP 4.18 / 4.29).

Verifies whole vs fractional share rules and the minimum-quantity boundary,
with parameters from the configuration.
"""

import unittest

from harbor.core.backtest_domain import Market
from harbor.core.paper_order_rules import (
    OrderRuleStatus,
    UsOrderRuleConfig,
    apply_paper_order_rule,
    apply_us_order_rule,
)


class ApplyUsOrderRuleTests(unittest.TestCase):
    """US share rules (SP 4.18 / 4.29)."""

    def test_fractional_allowed_default(self) -> None:
        outcome = apply_us_order_rule(100.5)
        self.assertEqual(outcome.status, OrderRuleStatus.OK)
        self.assertEqual(outcome.quantity, 100.5)

    def test_fractional_below_min_rejected(self) -> None:
        outcome = apply_us_order_rule(0.5)
        self.assertEqual(outcome.status, OrderRuleStatus.REJECTED)
        self.assertEqual(outcome.quantity, 0.0)
        self.assertFalse(outcome.ok)
        self.assertIn("below minimum", outcome.reason or "")

    def test_fractional_with_low_min_accepted(self) -> None:
        outcome = apply_us_order_rule(
            0.5, config=UsOrderRuleConfig(allow_fractional=True, min_quantity=0.01)
        )
        self.assertEqual(outcome.status, OrderRuleStatus.OK)
        self.assertEqual(outcome.quantity, 0.5)

    def test_whole_shares_round_down(self) -> None:
        outcome = apply_us_order_rule(100.7, config=UsOrderRuleConfig(allow_fractional=False))
        self.assertEqual(outcome.status, OrderRuleStatus.ADJUSTED)
        self.assertEqual(outcome.quantity, 100.0)
        self.assertIn("whole shares", outcome.reason or "")

    def test_whole_shares_below_min_rejected(self) -> None:
        outcome = apply_us_order_rule(0.9, config=UsOrderRuleConfig(allow_fractional=False))
        self.assertEqual(outcome.status, OrderRuleStatus.REJECTED)

    def test_integer_ok(self) -> None:
        outcome = apply_us_order_rule(100.0)
        self.assertEqual(outcome.status, OrderRuleStatus.OK)

    def test_custom_min_quantity(self) -> None:
        outcome = apply_us_order_rule(
            10.0, config=UsOrderRuleConfig(allow_fractional=False, min_quantity=5.0)
        )
        self.assertEqual(outcome.status, OrderRuleStatus.OK)

    def test_non_positive_rejected(self) -> None:
        with self.assertRaises(ValueError):
            apply_us_order_rule(0.0)

    def test_config_validated(self) -> None:
        import pydantic

        with self.assertRaises(pydantic.ValidationError):
            UsOrderRuleConfig(min_quantity=0.0)


class DispatchTests(unittest.TestCase):
    """The per-market dispatch routes US orders to US rules (SP 4.18)."""

    def test_dispatch_us(self) -> None:
        outcome = apply_paper_order_rule(Market.US, 100.5)
        self.assertEqual(outcome.status, OrderRuleStatus.OK)
        self.assertEqual(outcome.quantity, 100.5)
        self.assertIn("US", outcome.readable())
