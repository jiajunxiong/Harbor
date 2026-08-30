"""Paper replay trace tests (MVP 4 / SP 4.59).

Verifies that two runs with equal inputs produce equal execution traces and
fingerprints, and that the fingerprint excludes the run id.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import CashBalance, Currency, Market, NetValue, OrderSide
from harbor.core.paper_domain import (
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
)
from harbor.core.paper_replay import (
    PaperExecutionTrace,
    PaperReplayError,
    build_paper_execution_trace,
    paper_execution_trace_fingerprint,
)
from harbor.core.paper_valuation import PaperValuation

_TRADE = date(2026, 1, 2)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _order(order_id: str = "order-1") -> PaperOrder:
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
        intention_id="sig-1",
    )


def _fill() -> PaperFill:
    return PaperFill(
        fill_id="fill-1",
        paper_order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=OrderSide.BUY,
        quantity=100.0,
        price=50.0,
        currency=Currency.HKD,
        trade_date=_TRADE,
        fee=5.0,
    )


def _valuation() -> PaperValuation:
    net = NetValue(
        as_of_date=_TRADE, currency=Currency.HKD, cash=994_995.0, securities_value=5_500.0
    )
    return PaperValuation(
        as_of=_TRADE,
        base_currency=Currency.HKD,
        cash=(CashBalance(Currency.HKD, 994_995.0),),
        position_values=(),
        fees_paid=(CashBalance(Currency.HKD, 5.0),),
        cash_base=994_995.0,
        securities_base=5_500.0,
        fees_base=5.0,
        net_value=net,
        missing_prices=(),
    )


class PaperExecutionTraceTests(unittest.TestCase):
    """The replayable trace (SP 4.59)."""

    def test_build_and_readable(self) -> None:
        trace = build_paper_execution_trace(
            run_id="paper-1",
            orders=(_order(),),
            fills=(_fill(),),
            valuations=(_valuation(),),
            audit_fingerprint="abc",
        )
        self.assertIsInstance(trace, PaperExecutionTrace)
        self.assertIn("paper execution trace paper-1", trace.readable())

    def test_equal_traces_replay_identically(self) -> None:
        first = build_paper_execution_trace(run_id="paper-1", orders=(_order(),), fills=(_fill(),))
        second = build_paper_execution_trace(run_id="paper-1", orders=(_order(),), fills=(_fill(),))
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint(), second.fingerprint())

    def test_fingerprint_excludes_run_id(self) -> None:
        first = build_paper_execution_trace(run_id="paper-1", orders=(_order(),))
        second = build_paper_execution_trace(run_id="paper-2", orders=(_order(),))
        self.assertEqual(first.fingerprint(), second.fingerprint())

    def test_fingerprint_changes_with_content(self) -> None:
        base = build_paper_execution_trace(run_id="paper-1", orders=(_order(),)).fingerprint()
        changed_order = build_paper_execution_trace(
            run_id="paper-1", orders=(_order(order_id="order-2"),)
        ).fingerprint()
        self.assertNotEqual(base, changed_order)
        changed_fill = build_paper_execution_trace(
            run_id="paper-1", orders=(_order(),), fills=(_fill(),)
        ).fingerprint()
        self.assertNotEqual(base, changed_fill)
        changed_audit = build_paper_execution_trace(
            run_id="paper-1", orders=(_order(),), audit_fingerprint="xyz"
        ).fingerprint()
        self.assertNotEqual(base, changed_audit)

    def test_fingerprint_helper(self) -> None:
        trace = build_paper_execution_trace(run_id="paper-1", orders=(_order(),))
        self.assertEqual(paper_execution_trace_fingerprint(trace), trace.fingerprint())

    def test_empty_run_id_rejected(self) -> None:
        with self.assertRaises(PaperReplayError):
            build_paper_execution_trace(run_id="")
