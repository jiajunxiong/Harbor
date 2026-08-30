"""Paper fill simulation tests (MVP 4 / SP 4.22).

Verifies that the fill price follows the fill rule (open / close / next open),
that slippage moves the price in the trade direction and is recorded
explicitly, and that the volume participation rate caps the fill quantity.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_config import FillRule
from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.paper_domain import (
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
)
from harbor.core.paper_fill_simulation import (
    PaperFillSimulationError,
    simulate_paper_fill,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _order(side: OrderSide = OrderSide.BUY, quantity: float = 100.0) -> PaperOrder:
    return PaperOrder(
        order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=side,
        quantity=quantity,
        currency=Currency.HKD,
        price_type=PaperPriceType.REFERENCE,
        status=PaperOrderStatus.CREATED,
        created_at=_utc_at(2026, 1, 2, 9),
    )


def _quote(close: float, open_: float = 0.0, day: date | None = None) -> DailyQuote:
    return DailyQuote(
        market=Market.HK,
        symbol="0001.HK",
        day=day or date(2026, 1, 2),
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=100_000,
        adjusted_close=close,
    )


class FillPriceTests(unittest.TestCase):
    """The fill price follows the rule (SP 4.22 / 2.39)."""

    def test_close_rule(self) -> None:
        result = simulate_paper_fill(
            order=_order(),
            rule=FillRule.CLOSE,
            quote=_quote(close=50.0, open_=49.0),
        )
        self.assertEqual(result.reference_price, 50.0)
        self.assertIsNotNone(result.fill)
        self.assertEqual(result.fill.price, 50.0)
        self.assertEqual(result.fill.trade_date, date(2026, 1, 2))

    def test_open_rule(self) -> None:
        result = simulate_paper_fill(
            order=_order(),
            rule=FillRule.OPEN,
            quote=_quote(close=50.0, open_=49.0),
        )
        self.assertEqual(result.reference_price, 49.0)

    def test_next_open_rule(self) -> None:
        result = simulate_paper_fill(
            order=_order(),
            rule=FillRule.NEXT_OPEN,
            quote=_quote(close=50.0),
            next_quote=_quote(open_=51.0, close=51.0, day=date(2026, 1, 5)),
        )
        self.assertEqual(result.reference_price, 51.0)
        self.assertEqual(result.fill.trade_date, date(2026, 1, 5))

    def test_next_open_requires_next_quote(self) -> None:
        with self.assertRaises(ValueError):
            simulate_paper_fill(
                order=_order(),
                rule=FillRule.NEXT_OPEN,
                quote=_quote(close=50.0),
            )


class SlippageTests(unittest.TestCase):
    """Slippage is applied directionally and recorded (SP 4.22)."""

    def test_buy_pays_up(self) -> None:
        result = simulate_paper_fill(
            order=_order(side=OrderSide.BUY),
            quote=_quote(close=100.0),
            slippage_bps=50,
        )
        self.assertAlmostEqual(result.exec_price, 100.5, places=6)
        self.assertEqual(result.fill.price, result.exec_price)
        self.assertEqual(result.slippage_bps, 50)
        self.assertIn("100.5", result.readable())

    def test_sell_receives_less(self) -> None:
        result = simulate_paper_fill(
            order=_order(side=OrderSide.SELL),
            quote=_quote(close=100.0),
            slippage_bps=50,
        )
        self.assertAlmostEqual(result.exec_price, 99.5, places=6)


class VolumeLimitTests(unittest.TestCase):
    """The participation rate caps the fill (SP 4.22 / 2.40)."""

    def test_volume_caps_quantity(self) -> None:
        result = simulate_paper_fill(
            order=_order(quantity=1000.0),
            quote=_quote(close=50.0),
            participation_rate=0.1,
            volume=5000,
        )
        self.assertTrue(result.volume_limited)
        self.assertEqual(result.filled_quantity, 500.0)  # 0.1 * 5000
        self.assertEqual(result.fill.quantity, 500.0)
        self.assertIn("capped", result.basis)

    def test_volume_leaves_nothing(self) -> None:
        result = simulate_paper_fill(
            order=_order(quantity=1000.0),
            quote=_quote(close=50.0),
            participation_rate=0.0,
            volume=100,
        )
        self.assertEqual(result.filled_quantity, 0.0)
        self.assertIsNone(result.fill)
        self.assertIn("nothing to fill", result.basis)

    def test_partial_policy_requires_both(self) -> None:
        with self.assertRaises(PaperFillSimulationError):
            simulate_paper_fill(
                order=_order(),
                quote=_quote(close=50.0),
                participation_rate=0.1,
            )
        with self.assertRaises(PaperFillSimulationError):
            simulate_paper_fill(
                order=_order(),
                quote=_quote(close=50.0),
                volume=1000,
            )
