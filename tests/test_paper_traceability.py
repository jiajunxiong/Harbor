"""Paper order traceability tests (MVP 4 / SP 4.25).

Verifies that every order links to its signal intention, strategy version,
rebalance day and source MVP 2/3 research run, and that a mismatched linkage
is refused.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_domain import (
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
    SignalIntention,
)
from harbor.core.paper_traceability import (
    OrderTrace,
    PaperTraceabilityError,
    build_order_trace,
)

_TRADE = date(2026, 1, 2)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _intention(intention_id: str = "sig-1") -> SignalIntention:
    return SignalIntention(
        intention_id=intention_id,
        paper_run_id="paper-1",
        strategy="mvp3-qualified",
        strategy_version="1.0.0",
        market=Market.HK,
        rebalance_date=_TRADE,
        target_weights=(("0001.HK", 1.0),),
        source_run_id="mvp3-run-1",
        created_at=_utc_at(2026, 1, 1, 12),
    )


def _order(intention_id: str | None = "sig-1", order_id: str = "order-1") -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=OrderSide.BUY,
        quantity=100.0,
        currency=Currency.HKD,
        price_type=PaperPriceType.REFERENCE,
        status=PaperOrderStatus.CREATED,
        created_at=_utc_at(2026, 1, 2, 9),
        intention_id=intention_id,
    )


class BuildOrderTraceTests(unittest.TestCase):
    """Building the order trace from the signal (SP 4.25)."""

    def test_derives_from_intention(self) -> None:
        order = _order()
        trace = build_order_trace(order=order, intention=_intention())
        self.assertIsInstance(trace, OrderTrace)
        self.assertEqual(trace.strategy, "mvp3-qualified")
        self.assertEqual(trace.strategy_version, "1.0.0")
        self.assertEqual(trace.rebalance_date, _TRADE)
        self.assertEqual(trace.source_run_id, "mvp3-run-1")
        self.assertTrue(trace.verifies)
        trace.verify()  # does not raise

    def test_mismatched_intention_refused(self) -> None:
        order = _order(intention_id="sig-other")
        trace = build_order_trace(order=order, intention=_intention())
        self.assertFalse(trace.verifies)
        with self.assertRaises(PaperTraceabilityError):
            trace.verify()

    def test_explicit_args_without_intention(self) -> None:
        order = _order(intention_id=None)
        trace = build_order_trace(
            order=order,
            strategy="mvp3-qualified",
            strategy_version="1.0.0",
            rebalance_date=_TRADE,
            source_run_id="mvp3-run-1",
        )
        self.assertEqual(trace.strategy, "mvp3-qualified")
        self.assertEqual(trace.source_run_id, "mvp3-run-1")
        self.assertTrue(trace.verifies)

    def test_missing_identity_rejected(self) -> None:
        order = _order()
        with self.assertRaises(PaperTraceabilityError):
            build_order_trace(order=order)

    def test_readable(self) -> None:
        trace = build_order_trace(order=_order(), intention=_intention())
        rendered = trace.readable()
        self.assertIn("order-1", rendered)
        self.assertIn("sig-1", rendered)
        self.assertIn("mvp3-run-1", rendered)
