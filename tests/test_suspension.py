"""Suspension / untradeable handling tests (MVP 2 / SP 2.41).

Verifies that suspended symbols (no quote or zero volume) are forbidden from
new fills with a reason, that existing positions are valued at the last
available close with a warning, and that no price is fabricated when none is
available.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import SuspensionConfig, SuspensionValuation
from harbor.core.backtest_domain import Currency, Market, Order, OrderSide
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.suspension import (
    PositionValuation,
    RefusedOrder,
    SuspensionWarning,
    is_tradeable,
    position_valuation_price,
    refuse_order,
)

_BUY = OrderSide.BUY

_DAY = date(2024, 1, 2)
_LAST = date(2023, 12, 29)


def _quote(
    *,
    day: date = _DAY,
    close: float = 11.0,
    volume: int = 1_000,
    market: Market = Market.HK,
    symbol: str = "0001.HK",
) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _order(*, symbol: str = "0001.HK", quantity: float = 800.0, ref: str = "r1") -> Order:
    return Order(
        symbol=symbol,
        market=Market.HK,
        side=_BUY,
        quantity=quantity,
        currency=Currency.HKD,
        trade_date=_DAY,
        ref=ref,
    )


class TradeabilityTests(unittest.TestCase):
    """Verify what counts as tradeable."""

    def test_tradeable_with_volume(self) -> None:
        self.assertTrue(is_tradeable(_quote(volume=1_000)))

    def test_missing_quote_is_untradeable(self) -> None:
        self.assertFalse(is_tradeable(None))

    def test_zero_volume_is_untradeable(self) -> None:
        self.assertFalse(is_tradeable(_quote(volume=0)))


class RefuseOrderTests(unittest.TestCase):
    """Verify suspended symbols cannot produce new fills."""

    def test_tradeable_order_is_not_refused(self) -> None:
        self.assertIsNone(refuse_order(order=_order(), day=_DAY, quote=_quote()))

    def test_missing_quote_refuses_with_reason(self) -> None:
        refusal = refuse_order(order=_order(), day=_DAY, quote=None)
        self.assertIsInstance(refusal, RefusedOrder)
        self.assertIsNotNone(refusal)
        if refusal is not None:
            self.assertIn("no quote", refusal.reason)
            self.assertEqual(refusal.day, _DAY)
            self.assertIn("BUY 0001.HK", refusal.readable())

    def test_zero_volume_refuses_with_reason(self) -> None:
        refusal = refuse_order(order=_order(), day=_DAY, quote=_quote(volume=0))
        self.assertIsNotNone(refusal)
        if refusal is not None:
            self.assertIn("zero volume", refusal.reason)

    def test_refusal_carries_order(self) -> None:
        order = _order(ref="r9")
        refusal = refuse_order(order=order, day=_DAY, quote=None)
        self.assertIsNotNone(refusal)
        if refusal is not None:
            self.assertIs(refusal.order, order)


class PositionValuationTests(unittest.TestCase):
    """Verify positions are valued at the last available price with a warning."""

    def test_uses_day_close_when_quote_exists(self) -> None:
        quote = _quote(close=12.5)
        val = position_valuation_price(
            market=Market.HK, symbol="0001.HK", day=_DAY, quote=quote, last_quote=quote
        )
        self.assertIsInstance(val, PositionValuation)
        self.assertAlmostEqual(val.price, 12.5)
        self.assertFalse(val.carried_forward)
        self.assertIsNone(val.warning)

    def test_carries_last_close_with_warning(self) -> None:
        last = _quote(day=_LAST, close=10.0)
        val = position_valuation_price(
            market=Market.HK, symbol="0001.HK", day=_DAY, quote=None, last_quote=last
        )
        self.assertTrue(val.carried_forward)
        self.assertAlmostEqual(val.price, 10.0)
        self.assertIsInstance(val.warning, SuspensionWarning)
        if val.warning is not None:
            self.assertIn("last available close 10.0000", val.warning.message)
            self.assertEqual(val.warning.day, _DAY)
            self.assertIn("0001.HK", val.readable())

    def test_warning_can_be_disabled(self) -> None:
        config = SuspensionConfig(valuation=SuspensionValuation.LAST_PRICE, warn=False)
        last = _quote(day=_LAST, close=10.0)
        val = position_valuation_price(
            market=Market.HK,
            symbol="0001.HK",
            day=_DAY,
            quote=None,
            last_quote=last,
            config=config,
        )
        self.assertTrue(val.carried_forward)
        self.assertIsNone(val.warning)

    def test_no_price_at_all_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "No price available"):
            position_valuation_price(
                market=Market.HK, symbol="0001.HK", day=_DAY, quote=None, last_quote=None
            )

    def test_repeat_valuation_identical(self) -> None:
        last = _quote(day=_LAST, close=10.0)
        kwargs = dict(
            market=Market.HK,
            symbol="0001.HK",
            day=_DAY,
            quote=None,
            last_quote=last,
        )
        first = position_valuation_price(**kwargs)
        second = position_valuation_price(**kwargs)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
