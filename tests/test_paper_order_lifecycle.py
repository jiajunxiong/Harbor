"""Paper order lifecycle suite (MVP 4 / SP 4.30).

Consolidated tests over SP 4.20-4.26 covering the acceptance dimensions:
取消 (cancel), 拒绝 (reject), 部分成交 (partial fill), 重复提交 (duplicate
submission is idempotent) and 非法迁移 (illegal transitions are rejected).
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
from harbor.core.paper_fill_simulation import simulate_paper_fill
from harbor.core.paper_order_idempotency import (
    OrderRegistry,
    order_idempotency_key,
    resolve_order,
)
from harbor.core.paper_order_state_machine import (
    PaperOrderStateError,
    paper_order_initial_state,
)
from harbor.core.paper_unfilled import decide_unfilled

_TRADE = date(2026, 1, 2)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _order(order_id: str = "order-1", quantity: float = 1000.0) -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=OrderSide.BUY,
        quantity=quantity,
        currency=Currency.HKD,
        price_type=PaperPriceType.REFERENCE,
        status=PaperOrderStatus.CREATED,
        created_at=_utc_at(2026, 1, 2, 9),
        intention_id="sig-1",
    )


def _quote(close: float = 50.0) -> DailyQuote:
    return DailyQuote(
        market=Market.HK,
        symbol="0001.HK",
        day=_TRADE,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=10_000,
        adjusted_close=close,
    )


class CancelTests(unittest.TestCase):
    """Cancelling an order at various stages (SP 4.30 / 4.20)."""

    def test_cancel_from_created(self) -> None:
        state = paper_order_initial_state("order-1").cancel(reason="manual cancel")
        self.assertEqual(state.status, PaperOrderStatus.CANCELLED)

    def test_cancel_from_submitted(self) -> None:
        state = paper_order_initial_state("order-1").submit().cancel(reason="manual cancel")
        self.assertEqual(state.status, PaperOrderStatus.CANCELLED)

    def test_cancel_remainder_after_partial(self) -> None:
        state = (
            paper_order_initial_state("order-1")
            .submit()
            .fill_partial()
            .cancel(reason="remainder cancelled")
        )
        self.assertEqual(state.status, PaperOrderStatus.CANCELLED)

    def test_cancel_terminal_rejected(self) -> None:
        state = paper_order_initial_state("order-1").cancel()
        with self.assertRaises(PaperOrderStateError):
            state.cancel()


class RejectTests(unittest.TestCase):
    """Rejecting an order with a mandatory reason (SP 4.30 / 4.20)."""

    def test_reject_from_created(self) -> None:
        state = paper_order_initial_state("order-1").reject(reason="insufficient cash")
        self.assertEqual(state.status, PaperOrderStatus.REJECTED)

    def test_reject_from_submitted(self) -> None:
        state = paper_order_initial_state("order-1").submit().reject(reason="risk gate")
        self.assertEqual(state.status, PaperOrderStatus.REJECTED)


class PartialFillTests(unittest.TestCase):
    """Partial fills via the volume limit keep the unfilled reason (SP 4.30)."""

    def test_partial_fill_lifecycle(self) -> None:
        order = _order(quantity=1000.0)
        state = paper_order_initial_state(order.order_id).submit()
        # 0.05 participation * 10,000 volume caps the fill to 500 -> partial
        simulation = simulate_paper_fill(
            order=order,
            rule=FillRule.CLOSE,
            quote=_quote(close=50.0),
            participation_rate=0.05,
            volume=10_000,
        )
        # capped to 0.05 * 10000 = 500 -> partial
        self.assertEqual(simulation.filled_quantity, 500.0)
        state = state.fill_partial(reason="volume participation capped to 500")
        outcome = decide_unfilled(
            order_id=order.order_id,
            requested_quantity=order.quantity,
            filled_quantity=simulation.filled_quantity,
            policy=UnfilledPolicy.DEFER,
            reason="volume participation capped to 500",
        )
        self.assertTrue(outcome.is_partial)
        self.assertEqual(outcome.deferred_quantity, 500.0)
        # remainder fills on the next day -> complete
        state = state.complete(reason="remainder filled")
        self.assertEqual(state.status, PaperOrderStatus.FILLED)


class DuplicateSubmissionTests(unittest.TestCase):
    """Duplicate submission is idempotent (SP 4.30 / 4.26)."""

    def test_resubmission_does_not_duplicate(self) -> None:
        first = _order("order-1")
        key = order_idempotency_key(
            paper_run_id=first.paper_run_id,
            intention_id=first.intention_id or "",
            market=first.market,
            symbol=first.symbol,
            side=first.side,
        )
        registry = OrderRegistry()
        registry, resolved = resolve_order(registry, first, key=key)
        registry, resolved_again = resolve_order(registry, _order("order-2"), key=key)
        self.assertEqual(resolved_again, first)
        self.assertEqual(len(registry.entries), 1)


class IllegalTransitionTests(unittest.TestCase):
    """Illegal order-lifecycle transitions are rejected (SP 4.30 / 4.20)."""

    def test_created_cannot_complete(self) -> None:
        with self.assertRaises(PaperOrderStateError):
            paper_order_initial_state("order-1").complete()

    def test_submitted_cannot_resubmit(self) -> None:
        state = paper_order_initial_state("order-1").submit()
        with self.assertRaises(PaperOrderStateError):
            state.submit()

    def test_filled_is_terminal(self) -> None:
        state = paper_order_initial_state("order-1").submit().complete()
        with self.assertRaises(PaperOrderStateError):
            state.fill_partial()
        with self.assertRaises(PaperOrderStateError):
            state.cancel()
