"""Unfilled paper order handling (MVP 4 / SP 4.21).

Keeps the unfilled reason of a partially-filled or unfilled paper order and
decides whether the remainder is cancelled or deferred to the next trading day
according to the configured :class:`~harbor.core.backtest_config.UnfilledPolicy`
(SP 2.40). An order is never silently dropped: the outcome records the filled
and unfilled quantities, the policy and a human-readable reason.

Pure core logic: depends on the backtest configuration (SP 2.40) and never
touches storage or CLI code.
"""

from dataclasses import dataclass

from harbor.core.backtest_config import UnfilledPolicy


class PaperUnfilledError(ValueError):
    """Raised when an unfilled outcome is invalid (SP 4.21)."""


@dataclass(frozen=True)
class PaperUnfilledOutcome:
    """The recorded handling of an order that did not fully fill (SP 4.21)."""

    order_id: str
    requested_quantity: float
    filled_quantity: float
    unfilled_quantity: float
    policy: UnfilledPolicy
    reason: str

    def __post_init__(self) -> None:
        if not self.order_id:
            raise PaperUnfilledError("order_id must be non-empty.")
        if self.requested_quantity <= 0:
            raise PaperUnfilledError("requested_quantity must be positive.")
        if not 0.0 <= self.filled_quantity <= self.requested_quantity:
            raise PaperUnfilledError("filled_quantity must be within [0, requested_quantity].")
        if not self.reason:
            raise PaperUnfilledError("An unfilled order must keep a reason.")
        if self.unfilled_quantity > 0 and not self.reason.strip():
            raise PaperUnfilledError("An unfilled order must keep a reason.")

    @property
    def is_full(self) -> bool:
        """Whether the order filled completely."""
        return self.unfilled_quantity == 0.0

    @property
    def is_partial(self) -> bool:
        """Whether the order filled only part of its requested quantity."""
        return 0.0 < self.filled_quantity < self.requested_quantity

    @property
    def is_unfilled(self) -> bool:
        """Whether the order filled nothing at all."""
        return self.filled_quantity == 0.0 and self.unfilled_quantity > 0.0

    @property
    def deferred_quantity(self) -> float:
        """Quantity carried to the next day under the ``DEFER`` policy."""
        return self.unfilled_quantity if self.policy is UnfilledPolicy.DEFER else 0.0

    @property
    def cancelled_quantity(self) -> float:
        """Quantity dropped under the ``CANCEL`` policy."""
        return self.unfilled_quantity if self.policy is UnfilledPolicy.CANCEL else 0.0

    def readable(self) -> str:
        """Render the outcome as a compact summary."""
        return (
            f"order {self.order_id}: filled {self.filled_quantity:g} / "
            f"requested {self.requested_quantity:g} "
            f"(policy {self.policy.value}, deferred {self.deferred_quantity:g}, "
            f"cancelled {self.cancelled_quantity:g}) — {self.reason}"
        )


def decide_unfilled(
    *,
    order_id: str,
    requested_quantity: float,
    filled_quantity: float,
    policy: UnfilledPolicy,
    reason: str,
) -> PaperUnfilledOutcome:
    """Record how an order's unfilled remainder is handled (SP 4.21).

    The reason is always preserved (未完成订单保留原因); the policy decides
    whether the remainder is deferred or cancelled, never silently dropped.
    """
    unfilled = max(0.0, requested_quantity - filled_quantity)
    return PaperUnfilledOutcome(
        order_id=order_id,
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        unfilled_quantity=unfilled,
        policy=policy,
        reason=reason,
    )
