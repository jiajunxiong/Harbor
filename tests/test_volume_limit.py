"""Volume-participation constraint tests (MVP 2 / SP 2.40).

Verifies the traded-value participation-rate cap on fill quantity, the full /
partial / unfilled classification with a human-readable reason, and that the
unfilled portion is cancelled or deferred according to the configured policy.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import UnfilledPolicy
from harbor.core.backtest_domain import Currency, Market, Order, OrderSide
from harbor.core.volume_limit import apply_volume_limit, limit_fill_quantity

_BUY = OrderSide.BUY

_DAY = date(2024, 1, 2)


def _order(
    *,
    symbol: str = "0001.HK",
    quantity: float = 800.0,
    side: OrderSide = _BUY,
    ref: str = "rebalance-1",
) -> Order:
    return Order(
        symbol=symbol,
        market=Market.HK,
        side=side,
        quantity=quantity,
        currency=Currency.HKD,
        trade_date=_DAY,
        ref=ref,
    )


class LimitFillQuantityTests(unittest.TestCase):
    """Verify the participation-rate quantity cap."""

    def test_below_cap_is_unchanged(self) -> None:
        # Volume 5000 * rate 0.1 -> cap 500; requested 100 fills in full.
        self.assertEqual(
            limit_fill_quantity(
                quantity=100.0,
                reference_price=11.0,
                volume=5_000,
                participation_rate=0.1,
            ),
            100.0,
        )

    def test_caps_at_participation_times_volume(self) -> None:
        self.assertEqual(
            limit_fill_quantity(
                quantity=1_000.0,
                reference_price=11.0,
                volume=5_000,
                participation_rate=0.1,
            ),
            500.0,
        )

    def test_zero_volume_allows_nothing(self) -> None:
        self.assertEqual(
            limit_fill_quantity(
                quantity=1_000.0,
                reference_price=11.0,
                volume=0,
                participation_rate=0.1,
            ),
            0.0,
        )

    def test_rate_one_allows_up_to_volume(self) -> None:
        self.assertEqual(
            limit_fill_quantity(
                quantity=2_000.0,
                reference_price=11.0,
                volume=1_500,
                participation_rate=1.0,
            ),
            1_500.0,
        )

    def test_rate_zero_allows_nothing(self) -> None:
        self.assertEqual(
            limit_fill_quantity(
                quantity=1_000.0,
                reference_price=11.0,
                volume=5_000,
                participation_rate=0.0,
            ),
            0.0,
        )

    def test_rejects_non_positive_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "quantity"):
            limit_fill_quantity(
                quantity=0.0,
                reference_price=11.0,
                volume=5_000,
                participation_rate=0.1,
            )

    def test_rejects_non_positive_price(self) -> None:
        with self.assertRaisesRegex(ValueError, "reference_price"):
            limit_fill_quantity(
                quantity=100.0,
                reference_price=0.0,
                volume=5_000,
                participation_rate=0.1,
            )

    def test_rejects_negative_volume(self) -> None:
        with self.assertRaisesRegex(ValueError, "volume"):
            limit_fill_quantity(
                quantity=100.0,
                reference_price=11.0,
                volume=-1,
                participation_rate=0.1,
            )

    def test_rejects_rate_out_of_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "participation_rate"):
            limit_fill_quantity(
                quantity=100.0,
                reference_price=11.0,
                volume=5_000,
                participation_rate=1.5,
            )


class ApplyVolumeLimitTests(unittest.TestCase):
    """Verify the full / partial / unfilled outcome and the unfilled policy."""

    def test_full_fill(self) -> None:
        outcome = apply_volume_limit(
            order=_order(quantity=100.0),
            reference_price=11.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        self.assertTrue(outcome.is_full)
        self.assertFalse(outcome.is_partial)
        self.assertFalse(outcome.is_unfilled)
        self.assertAlmostEqual(outcome.filled_quantity, 100.0)
        self.assertEqual(outcome.unfilled_quantity, 0.0)
        self.assertIn("fully filled", outcome.reason)

    def test_partial_fill_keeps_reason(self) -> None:
        outcome = apply_volume_limit(
            order=_order(quantity=1_000.0),
            reference_price=11.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        self.assertFalse(outcome.is_full)
        self.assertTrue(outcome.is_partial)
        self.assertAlmostEqual(outcome.filled_quantity, 500.0)
        self.assertAlmostEqual(outcome.unfilled_quantity, 500.0)
        self.assertIn("partially filled", outcome.reason)

    def test_unfilled_keeps_reason(self) -> None:
        outcome = apply_volume_limit(
            order=_order(quantity=1_000.0),
            reference_price=11.0,
            volume=0,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        self.assertFalse(outcome.is_full)
        self.assertFalse(outcome.is_partial)
        self.assertTrue(outcome.is_unfilled)
        self.assertAlmostEqual(outcome.filled_quantity, 0.0)
        self.assertAlmostEqual(outcome.unfilled_quantity, 1_000.0)
        self.assertIn("unfilled", outcome.reason)

    def test_cancel_policy_drops_unfilled_portion(self) -> None:
        outcome = apply_volume_limit(
            order=_order(quantity=1_000.0),
            reference_price=11.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        self.assertAlmostEqual(outcome.cancelled_quantity, 500.0)
        self.assertEqual(outcome.deferred_quantity, 0.0)

    def test_defer_policy_carries_unfilled_portion(self) -> None:
        outcome = apply_volume_limit(
            order=_order(quantity=1_000.0),
            reference_price=11.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.DEFER,
        )
        self.assertAlmostEqual(outcome.deferred_quantity, 500.0)
        self.assertEqual(outcome.cancelled_quantity, 0.0)

    def test_carries_order_and_readable(self) -> None:
        order = _order(quantity=1_000.0, ref="rebalance-9")
        outcome = apply_volume_limit(
            order=order,
            reference_price=11.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.DEFER,
        )
        self.assertIs(outcome.order, order)
        summary = outcome.readable()
        self.assertIn("BUY 0001.HK", summary)
        self.assertIn("filled 500.00 / requested 1000.00", summary)
        self.assertIn("policy defer", summary)

    def test_repeat_application_identical(self) -> None:
        kwargs = dict(
            order=_order(quantity=1_000.0),
            reference_price=11.0,
            volume=5_000,
            participation_rate=0.1,
            policy=UnfilledPolicy.CANCEL,
        )
        first = apply_volume_limit(**kwargs)
        second = apply_volume_limit(**kwargs)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
