"""Paper order idempotency (MVP 4 / SP 4.26).

A paper order is identified by an idempotency key derived from its signal
identity: ``paper_run_id``, the signal intention id, the market, the symbol
and the side. Resubmitting the same signal produces the same key, so the
:class:`OrderRegistry` returns the existing order instead of creating a
duplicate (订单幂等). A duplicate registration is refused rather than silently
overwritten.

Pure core logic: depends on the paper domain and never touches storage or CLI
code.
"""

from dataclasses import dataclass

from harbor.core.backtest_domain import Market, OrderSide
from harbor.core.paper_domain import PaperOrder


class PaperOrderIdempotencyError(ValueError):
    """Raised when an order cannot be registered idempotently (SP 4.26)."""


def order_idempotency_key(
    *,
    paper_run_id: str,
    intention_id: str,
    market: Market,
    symbol: str,
    side: OrderSide,
) -> str:
    """Return the canonical idempotency key of a signal order (SP 4.26).

    Equal signal identities always produce the same key; the key includes the
    run, the intention, the market, the symbol and the side so a resubmitted
    signal never creates a duplicate order.
    """
    if not paper_run_id or not intention_id or not symbol:
        raise PaperOrderIdempotencyError("paper_run_id, intention_id and symbol must be non-empty.")
    return "|".join([paper_run_id, intention_id, market.value, symbol, side.value])


@dataclass(frozen=True)
class OrderRegistry:
    """An immutable registry of submitted paper orders (SP 4.26)."""

    entries: tuple[tuple[str, PaperOrder], ...] = ()

    def __post_init__(self) -> None:
        keys = [key for key, _order in self.entries]
        if len(set(keys)) != len(keys):
            raise PaperOrderIdempotencyError("Order registry keys must be unique.")
        if keys != sorted(keys):
            raise PaperOrderIdempotencyError("Order registry keys must be sorted.")

    def order_for_key(self, key: str) -> PaperOrder | None:
        """Return the order registered under ``key`` (None when absent)."""
        for registered_key, order in self.entries:
            if registered_key == key:
                return order
        return None

    def register(self, order: PaperOrder, *, key: str) -> "OrderRegistry":
        """Return a new registry with ``order`` registered under ``key``.

        Raises:
            PaperOrderIdempotencyError: If ``key`` is already registered (a
                resubmitted signal must reuse the existing order, not create a
                duplicate).
        """
        if self.order_for_key(key) is not None:
            raise PaperOrderIdempotencyError(
                f"An order is already registered under key {key!r}; "
                "resubmitting the same signal must not create a duplicate."
            )
        merged = tuple(sorted((*self.entries, (key, order)), key=lambda item: item[0]))
        return OrderRegistry(entries=merged)

    def readable(self) -> str:
        """Render the registry as a compact summary."""
        return f"order registry: {len(self.entries)} order(s)"


def resolve_order(
    registry: OrderRegistry,
    order: PaperOrder,
    *,
    key: str,
) -> tuple["OrderRegistry", PaperOrder]:
    """Return the existing order for ``key`` or register ``order`` (SP 4.26).

    Resubmitting the same signal (same key) returns the existing order and the
    unchanged registry; a new signal registers the new order. No duplicates
    are ever created.
    """
    existing = registry.order_for_key(key)
    if existing is not None:
        return registry, existing
    return registry.register(order, key=key), order
