"""Paper order idempotency tests (MVP 4 / SP 4.26).

Verifies that resubmitting the same signal produces the same idempotency key
and reuses the existing order, never creating a duplicate.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_domain import (
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
)
from harbor.core.paper_order_idempotency import (
    OrderRegistry,
    PaperOrderIdempotencyError,
    order_idempotency_key,
    resolve_order,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _order(order_id: str, **overrides: object) -> PaperOrder:
    fields: dict[str, object] = {
        "order_id": order_id,
        "paper_run_id": "paper-1",
        "market": Market.HK,
        "symbol": "0001.HK",
        "side": OrderSide.BUY,
        "quantity": 100.0,
        "currency": Currency.HKD,
        "price_type": PaperPriceType.REFERENCE,
        "status": PaperOrderStatus.CREATED,
        "created_at": _utc_at(2026, 1, 2, 9),
        "intention_id": "sig-1",
    }
    fields.update(overrides)
    return PaperOrder(**fields)  # type: ignore[arg-type]


def _key(order: PaperOrder) -> str:
    return order_idempotency_key(
        paper_run_id=order.paper_run_id,
        intention_id=order.intention_id or "",
        market=order.market,
        symbol=order.symbol,
        side=order.side,
    )


class OrderIdempotencyKeyTests(unittest.TestCase):
    """The canonical key (SP 4.26)."""

    def test_stable_for_same_signal(self) -> None:
        order = _order("o-1")
        self.assertEqual(_key(order), _key(_order("o-2")))

    def test_differs_on_identity(self) -> None:
        base = _key(_order("o-1"))
        self.assertNotEqual(base, _key(_order("o-2", symbol="0002.HK")))
        self.assertNotEqual(base, _key(_order("o-2", side=OrderSide.SELL)))
        self.assertNotEqual(base, _key(_order("o-2", market=Market.US)))
        self.assertNotEqual(base, _key(_order("o-2", intention_id="sig-2")))

    def test_requires_identity(self) -> None:
        with self.assertRaises(PaperOrderIdempotencyError):
            order_idempotency_key(
                paper_run_id="",
                intention_id="sig-1",
                market=Market.HK,
                symbol="0001.HK",
                side=OrderSide.BUY,
            )


class OrderRegistryTests(unittest.TestCase):
    """The idempotent registry (SP 4.26)."""

    def test_register_and_lookup(self) -> None:
        registry = OrderRegistry()
        order = _order("o-1")
        key = _key(order)
        registry = registry.register(order, key=key)
        self.assertEqual(registry.order_for_key(key), order)
        self.assertIsNone(registry.order_for_key("missing"))

    def test_duplicate_registration_rejected(self) -> None:
        registry = OrderRegistry()
        order = _order("o-1")
        key = _key(order)
        registry = registry.register(order, key=key)
        with self.assertRaises(PaperOrderIdempotencyError):
            registry.register(_order("o-2"), key=key)

    def test_immutable(self) -> None:
        registry = OrderRegistry()
        updated = registry.register(_order("o-1"), key=_key(_order("o-1")))
        self.assertEqual(registry.entries, ())
        self.assertEqual(len(updated.entries), 1)

    def test_readable(self) -> None:
        registry = OrderRegistry().register(_order("o-1"), key=_key(_order("o-1")))
        self.assertEqual(registry.readable(), "order registry: 1 order(s)")


class ResolveOrderTests(unittest.TestCase):
    """Resubmission reuses the existing order (SP 4.26)."""

    def test_new_signal_registers(self) -> None:
        registry = OrderRegistry()
        order = _order("o-1")
        key = _key(order)
        updated, resolved = resolve_order(registry, order, key=key)
        self.assertEqual(resolved, order)
        self.assertEqual(len(updated.entries), 1)

    def test_resubmission_reuses_existing(self) -> None:
        registry = OrderRegistry()
        first = _order("o-1")
        key = _key(first)
        registry, _ = resolve_order(registry, first, key=key)
        resubmitted = _order("o-2")
        updated, resolved = resolve_order(registry, resubmitted, key=key)
        self.assertEqual(resolved, first)  # the existing order, not a duplicate
        self.assertEqual(len(updated.entries), 1)
