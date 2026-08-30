"""Paper execution engine tests (MVP 4 / SP 4.50-4.52).

Verifies the execution engine: fill price per the rule, directional slippage,
volume-participation capping (SP 4.51) with the unfilled reason preserved, and
suspension refusal (SP 4.52).
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_config import FillRule, UnfilledPolicy
from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.paper_domain import (
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
)
from harbor.core.paper_execution_engine import (
    PaperExecutionError,
    PaperExecutionResult,
    execute_paper_order,
)

_TRADE = date(2026, 1, 2)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _order(quantity: float = 1000.0, **overrides: object) -> PaperOrder:
    fields: dict[str, object] = {
        "order_id": "order-1",
        "paper_run_id": "paper-1",
        "market": Market.HK,
        "symbol": "0001.HK",
        "side": OrderSide.BUY,
        "quantity": quantity,
        "currency": Currency.HKD,
        "price_type": PaperPriceType.REFERENCE,
        "status": PaperOrderStatus.CREATED,
        "created_at": _utc_at(2026, 1, 2, 9),
        "intention_id": "sig-1",
    }
    fields.update(overrides)
    return PaperOrder(**fields)  # type: ignore[arg-type]


def _quote(close: float, volume: int = 100_000, **overrides: object) -> DailyQuote:
    fields: dict[str, object] = {
        "market": Market.HK,
        "symbol": "0001.HK",
        "day": _TRADE,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "adjusted_close": close,
    }
    fields.update(overrides)
    return DailyQuote(**fields)  # type: ignore[arg-type]


class ExecutionEngineTests(unittest.TestCase):
    """The execution engine (SP 4.50)."""

    def test_full_fill(self) -> None:
        result = execute_paper_order(order=_order(), day=_TRADE, quote=_quote(50.0))
        self.assertIsInstance(result, PaperExecutionResult)
        self.assertTrue(result.executed)
        self.assertIsNotNone(result.fill)
        self.assertEqual(result.fill.price, 50.0)
        self.assertEqual(result.fill.quantity, 1000.0)
        self.assertIsNone(result.unfilled)

    def test_slippage_moves_price(self) -> None:
        result = execute_paper_order(
            order=_order(), day=_TRADE, quote=_quote(100.0), slippage_bps=50
        )
        self.assertAlmostEqual(result.fill.price, 100.5, places=6)

    def test_volume_participation_partial(self) -> None:
        result = execute_paper_order(
            order=_order(quantity=1000.0),
            day=_TRADE,
            quote=_quote(50.0, volume=10_000),
            participation_rate=0.05,
            volume=10_000,
        )
        self.assertTrue(result.executed)
        self.assertEqual(result.fill.quantity, 500.0)  # 0.05 * 10,000
        self.assertIsNotNone(result.unfilled)
        self.assertEqual(result.unfilled.filled_quantity, 500.0)
        self.assertEqual(result.unfilled.unfilled_quantity, 500.0)
        self.assertEqual(result.unfilled.cancelled_quantity, 500.0)  # CANCEL policy

    def test_partial_defer_policy(self) -> None:
        result = execute_paper_order(
            order=_order(quantity=1000.0),
            day=_TRADE,
            quote=_quote(50.0, volume=10_000),
            participation_rate=0.05,
            volume=10_000,
            unfilled_policy=UnfilledPolicy.DEFER,
        )
        self.assertEqual(result.unfilled.deferred_quantity, 500.0)

    def test_zero_participation_no_fill(self) -> None:
        result = execute_paper_order(
            order=_order(quantity=1000.0),
            day=_TRADE,
            quote=_quote(50.0, volume=100),
            participation_rate=0.0,
            volume=100,
        )
        self.assertFalse(result.executed)
        self.assertIsNone(result.fill)
        self.assertIsNotNone(result.unfilled)
        self.assertTrue(result.unfilled.is_unfilled)

    def test_suspension_refuses(self) -> None:
        result = execute_paper_order(order=_order(), day=_TRADE, quote=None)
        self.assertFalse(result.executed)
        self.assertTrue(result.suspended)
        self.assertIn("suspended or untradeable", result.refusal_reason or "")
        self.assertIn("0001.HK", result.readable())

    def test_zero_volume_refuses(self) -> None:
        result = execute_paper_order(order=_order(), day=_TRADE, quote=_quote(50.0, volume=0))
        self.assertTrue(result.suspended)

    def test_next_open_requires_next_quote(self) -> None:
        with self.assertRaises(ValueError):
            execute_paper_order(
                order=_order(),
                day=_TRADE,
                quote=_quote(50.0),
                rule=FillRule.NEXT_OPEN,
            )

    def test_participation_requires_volume(self) -> None:
        with self.assertRaises(PaperExecutionError):
            execute_paper_order(
                order=_order(),
                day=_TRADE,
                quote=_quote(50.0),
                participation_rate=0.1,
            )

    def test_readable(self) -> None:
        result = execute_paper_order(order=_order(), day=_TRADE, quote=_quote(50.0))
        self.assertIn("order order-1", result.readable())
