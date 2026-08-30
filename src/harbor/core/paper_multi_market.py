"""Multi-market paper order orchestration (MVP 4 / SP 4.24).

HK and US orders are generated and executed independently: orders are grouped
into per-market batches and never merged across markets (禁止跨市场隐式合并).
A single-market gate (:func:`assert_single_market`) refuses a mixed batch so a
cross-market order can never be silently combined.

Pure core logic: depends on the paper domain and never touches storage or CLI
code.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from harbor.core.backtest_domain import Market
from harbor.core.paper_domain import PaperOrder


class PaperMarketError(ValueError):
    """Raised when orders span markets unexpectedly (SP 4.24)."""


@dataclass(frozen=True)
class MarketOrderBatch:
    """One market's independent order batch (SP 4.24)."""

    market: Market
    orders: tuple[PaperOrder, ...]

    def __post_init__(self) -> None:
        if any(order.market is not self.market for order in self.orders):
            raise PaperMarketError(
                f"A batch for {self.market.value} must contain only {self.market.value} orders."
            )

    def readable(self) -> str:
        """Render the batch as a compact summary."""
        return (
            f"{self.market.value} batch: {len(self.orders)} order(s) "
            f"({', '.join(order.order_id for order in self.orders)})"
        )


def group_orders_by_market(orders: Sequence[PaperOrder]) -> tuple[MarketOrderBatch, ...]:
    """Group orders into independent per-market batches (SP 4.24).

    Orders are grouped by market and each batch is key-sorted by order id;
    orders are never merged across markets (HK and US stay separate).
    """
    by_market: dict[Market, list[PaperOrder]] = {}
    for order in orders:
        by_market.setdefault(order.market, []).append(order)
    batches: list[MarketOrderBatch] = []
    for market in sorted(by_market, key=lambda item: item.value):
        batch_orders = tuple(sorted(by_market[market], key=lambda order: order.order_id))
        batches.append(MarketOrderBatch(market=market, orders=batch_orders))
    return tuple(batches)


def assert_single_market(orders: Sequence[PaperOrder]) -> Market:
    """Return the single market of ``orders`` or refuse (SP 4.24).

    Raises:
        PaperMarketError: If ``orders`` is empty or spans more than one market.
    """
    if not orders:
        raise PaperMarketError("Cannot assert a single market for an empty batch.")
    markets = {order.market for order in orders}
    if len(markets) > 1:
        names = ", ".join(sorted(market.value for market in markets))
        raise PaperMarketError(
            f"Orders span multiple markets ({names}); cross-market implicit merging is forbidden."
        )
    return next(iter(markets))
