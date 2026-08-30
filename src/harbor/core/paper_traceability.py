"""Paper order traceability (MVP 4 / SP 4.25).

Every paper order is linked to its signal intention, the strategy and its
version, the rebalance day and the source MVP 2/3 research run, so the
signal -> order chain is fully traceable (信号与订单可追溯). The linkage is
derived from the signal intention when available and verified against the
order's own ``intention_id``; a mismatch is refused rather than silently
ignored.

Pure core logic: depends on the paper domain and never touches storage or CLI
code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.paper_domain import PaperOrder, SignalIntention


class PaperTraceabilityError(ValueError):
    """Raised when an order's trace cannot be established (SP 4.25)."""


@dataclass(frozen=True)
class OrderTrace:
    """The recorded signal -> order trace of one paper order (SP 4.25)."""

    order: PaperOrder
    strategy: str
    strategy_version: str
    rebalance_date: date
    intention_id: str | None
    source_run_id: str | None

    @property
    def verifies(self) -> bool:
        """Whether the order's intention id matches the traced intention."""
        return self.order.intention_id == self.intention_id

    def verify(self) -> None:
        """Raise unless the order's intention id matches (SP 4.25).

        Raises:
            PaperTraceabilityError: If the order does not link to the traced
                signal intention.
        """
        if not self.verifies:
            raise PaperTraceabilityError(
                f"order {self.order.order_id} links to intention "
                f"{self.order.intention_id or 'none'} but the traced signal "
                f"intention is {self.intention_id or 'none'}."
            )

    def readable(self) -> str:
        """Render the trace as a compact summary."""
        source = self.source_run_id or "none"
        intention = self.intention_id or "none"
        return (
            f"order {self.order.order_id} <- signal {intention} "
            f"({self.strategy}@{self.strategy_version} rebalance "
            f"{self.rebalance_date.isoformat()} source {source})"
        )


def build_order_trace(
    *,
    order: PaperOrder,
    intention: SignalIntention | None = None,
    strategy: str | None = None,
    strategy_version: str | None = None,
    rebalance_date: date | None = None,
    source_run_id: str | None = None,
) -> OrderTrace:
    """Build the trace of an order (SP 4.25).

    Strategy identity, version, rebalance day and the source research run are
    derived from the signal intention when supplied, otherwise from the
    explicit arguments.

    Raises:
        PaperTraceabilityError: If the strategy identity or rebalance day is
            missing.
    """
    if intention is not None:
        strategy = intention.strategy if strategy is None else strategy
        strategy_version = (
            intention.strategy_version if strategy_version is None else strategy_version
        )
        rebalance_date = intention.rebalance_date if rebalance_date is None else rebalance_date
        source_run_id = intention.source_run_id if source_run_id is None else source_run_id
    if not strategy or not strategy_version or rebalance_date is None:
        raise PaperTraceabilityError("strategy, strategy_version and rebalance_date are required.")
    return OrderTrace(
        order=order,
        strategy=strategy,
        strategy_version=strategy_version,
        rebalance_date=rebalance_date,
        intention_id=intention.intention_id if intention is not None else None,
        source_run_id=source_run_id,
    )
