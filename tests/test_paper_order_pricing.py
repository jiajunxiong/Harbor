"""Paper order pricing tests (MVP 4 / SP 4.19).

Verifies limit / market / reference price generation with a recorded pricing
basis and generation time, and directional slippage (buy pays up, sell
receives less).
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
from harbor.core.paper_order_pricing import (
    PaperOrderPrice,
    PaperOrderPricingError,
    price_paper_order,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _order(side: OrderSide = OrderSide.BUY, **overrides: object) -> PaperOrder:
    fields: dict[str, object] = {
        "order_id": "order-1",
        "paper_run_id": "paper-1",
        "market": Market.HK,
        "symbol": "0001.HK",
        "side": side,
        "quantity": 100.0,
        "currency": Currency.HKD,
        "price_type": PaperPriceType.REFERENCE,
        "status": PaperOrderStatus.CREATED,
        "created_at": _utc_at(2026, 1, 2, 9),
    }
    fields.update(overrides)
    return PaperOrder(**fields)  # type: ignore[arg-type]


def _quote(close: float, open_: float = 0.0) -> DailyQuote:
    return DailyQuote(
        market=Market.HK,
        symbol="0001.HK",
        day=date(2026, 1, 2),
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=100_000,
        adjusted_close=close,
    )


class LimitPricingTests(unittest.TestCase):
    """Limit order pricing (SP 4.19)."""

    def test_limit_price_used(self) -> None:
        result = price_paper_order(
            order=_order(),
            price_type=PaperPriceType.LIMIT,
            limit_price=49.5,
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertIsInstance(result, PaperOrderPrice)
        self.assertEqual(result.price, 49.5)
        self.assertIn("limit", result.basis)

    def test_limit_requires_price(self) -> None:
        with self.assertRaises(PaperOrderPricingError):
            price_paper_order(order=_order(), price_type=PaperPriceType.LIMIT)

    def test_defaults_to_order_price_type(self) -> None:
        result = price_paper_order(
            order=_order(price_type=PaperPriceType.LIMIT),
            limit_price=50.0,
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertEqual(result.price_type, PaperPriceType.LIMIT)


class MarketPricingTests(unittest.TestCase):
    """Market order pricing via the fill rule (SP 4.19 / 4.22)."""

    def test_close_rule(self) -> None:
        result = price_paper_order(
            order=_order(),
            price_type=PaperPriceType.MARKET,
            rule=FillRule.CLOSE,
            quote=_quote(close=50.0, open_=49.0),
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertEqual(result.reference_price, 50.0)
        self.assertIn("close", result.basis)

    def test_open_rule(self) -> None:
        result = price_paper_order(
            order=_order(),
            price_type=PaperPriceType.MARKET,
            rule=FillRule.OPEN,
            quote=_quote(close=50.0, open_=49.0),
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertEqual(result.reference_price, 49.0)

    def test_next_open_requires_next_quote(self) -> None:
        with self.assertRaises(ValueError):
            price_paper_order(
                order=_order(),
                price_type=PaperPriceType.MARKET,
                rule=FillRule.NEXT_OPEN,
                quote=_quote(close=50.0),
            )

    def test_market_requires_quote_or_reference(self) -> None:
        with self.assertRaises(PaperOrderPricingError):
            price_paper_order(
                order=_order(),
                price_type=PaperPriceType.MARKET,
                generated_at=_utc_at(2026, 1, 2, 9, 30),
            )

    def test_market_explicit_reference(self) -> None:
        result = price_paper_order(
            order=_order(),
            price_type=PaperPriceType.MARKET,
            reference_price=48.0,
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertEqual(result.reference_price, 48.0)


class ReferencePricingTests(unittest.TestCase):
    """Reference order pricing (SP 4.19)."""

    def test_reference_price_used(self) -> None:
        result = price_paper_order(
            order=_order(),
            price_type=PaperPriceType.REFERENCE,
            reference_price=50.0,
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertEqual(result.price, 50.0)
        self.assertIn("reference", result.basis)

    def test_reference_from_quote_close(self) -> None:
        result = price_paper_order(
            order=_order(),
            price_type=PaperPriceType.REFERENCE,
            quote=_quote(close=50.0),
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertEqual(result.price, 50.0)

    def test_reference_requires_input(self) -> None:
        with self.assertRaises(PaperOrderPricingError):
            price_paper_order(
                order=_order(),
                price_type=PaperPriceType.REFERENCE,
                generated_at=_utc_at(2026, 1, 2, 9, 30),
            )


class SlippageTests(unittest.TestCase):
    """Directional slippage is recorded in the basis (SP 4.19 / 4.22)."""

    def test_buy_pays_up(self) -> None:
        result = price_paper_order(
            order=_order(side=OrderSide.BUY),
            price_type=PaperPriceType.REFERENCE,
            reference_price=100.0,
            slippage_bps=50,
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertAlmostEqual(result.price, 100.5, places=6)
        self.assertEqual(result.slippage_bps, 50)
        self.assertIn("slippage 50 bps (up)", result.basis)

    def test_sell_receives_less(self) -> None:
        result = price_paper_order(
            order=_order(side=OrderSide.SELL),
            price_type=PaperPriceType.REFERENCE,
            reference_price=100.0,
            slippage_bps=50,
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertAlmostEqual(result.price, 99.5, places=6)
        self.assertIn("(down)", result.basis)

    def test_generated_at_defaults_utc(self) -> None:
        result = price_paper_order(
            order=_order(),
            price_type=PaperPriceType.REFERENCE,
            reference_price=50.0,
        )
        self.assertIsNotNone(result.generated_at.utcoffset())

    def test_readable(self) -> None:
        result = price_paper_order(
            order=_order(),
            price_type=PaperPriceType.REFERENCE,
            reference_price=50.0,
            generated_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertIn("order order-1", result.readable())
