"""Volume-participation constraint on order fills (MVP 2 / SP 2.40).

Limits how much of the day's traded value an order may consume, so a large
order is capped by the symbol's liquidity rather than filled in full against
thin volume. The cap follows the traded-value participation rate
(``participation_rate``, SP 2.4 :class:`~harbor.core.backtest_config.VolumeConfig`):
an order may consume at most ``participation_rate`` of the day's traded value
(``reference_price * volume``). Because the price cancels out, the resulting
quantity cap is simply ``participation_rate * volume``.

Orders that cannot fully fill keep a human-readable reason (未完成订单保留原因)
and are either cancelled or deferred to the next trading day according to the
configured :class:`~harbor.core.backtest_config.UnfilledPolicy` — fixed in the
configuration so a run is replayable.

Pure core logic: depends only on the domain types and the configuration; never
touches storage or CLI code.
"""

from dataclasses import dataclass

from harbor.core.backtest_config import UnfilledPolicy
from harbor.core.backtest_domain import Order


def limit_fill_quantity(
    *,
    quantity: float,
    reference_price: float,
    volume: int,
    participation_rate: float,
) -> float:
    """Cap a fill quantity by the traded-value participation rate (SP 2.40).

    The day's traded value is ``reference_price * volume``; the order may
    consume at most ``participation_rate`` of it, so the quantity cap is
    ``participation_rate * volume`` (the price cancels). Returns the smaller of
    ``quantity`` and that cap.

    Args:
        quantity: The requested fill quantity.
        reference_price: The fill reference price for the day (SP 2.39).
        volume: The day's traded volume for the symbol.
        participation_rate: The fraction of the day's traded value the order
            may consume.

    Raises:
        ValueError: If ``quantity`` or ``reference_price`` is not positive,
            ``volume`` is negative, or ``participation_rate`` is outside
            [0, 1].
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive.")
    if reference_price <= 0:
        raise ValueError("reference_price must be positive.")
    if volume < 0:
        raise ValueError("volume must be non-negative.")
    if not 0.0 <= participation_rate <= 1.0:
        raise ValueError("participation_rate must be within [0, 1].")
    max_fill_quantity = participation_rate * volume
    return min(quantity, max_fill_quantity)


@dataclass(frozen=True)
class VolumeLimitOutcome:
    """Outcome of applying the participation-rate rule to one order (SP 2.40).

    ``unfilled_quantity`` is the requested amount that did not fill; the
    configured ``policy`` decides whether it is cancelled or deferred to the
    next trading day. ``reason`` explains the outcome for the result / audit
    trail.
    """

    order: Order
    requested_quantity: float
    max_fill_quantity: float
    filled_quantity: float
    unfilled_quantity: float
    policy: UnfilledPolicy
    reason: str

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
        """Render the volume-limit outcome as a human-readable summary."""
        return (
            f"volume limit for {self.order.side.value} {self.order.symbol}: "
            f"filled {self.filled_quantity:.2f} / requested "
            f"{self.requested_quantity:.2f} (max {self.max_fill_quantity:.2f}, "
            f"policy {self.policy.value})\n"
            f"  {self.reason}"
        )


def apply_volume_limit(
    *,
    order: Order,
    reference_price: float,
    volume: int,
    participation_rate: float,
    policy: UnfilledPolicy,
) -> VolumeLimitOutcome:
    """Apply the traded-value participation-rate rule to one order (SP 2.40).

    Args:
        order: The order being filled.
        reference_price: The fill reference price for the day (SP 2.39).
        volume: The day's traded volume for the symbol.
        participation_rate: The fraction of the day's traded value the order
            may consume.
        policy: What happens to the unfilled portion (cancel or defer).

    Raises:
        ValueError: If ``reference_price`` is not positive, ``volume`` is
            negative, or ``participation_rate`` is outside [0, 1].
    """
    requested = order.quantity
    max_fill = limit_fill_quantity(
        quantity=requested,
        reference_price=reference_price,
        volume=volume,
        participation_rate=participation_rate,
    )
    filled = min(requested, max_fill)
    unfilled = requested - filled
    if unfilled == 0.0:
        reason = "fully filled within the volume participation limit."
    elif filled > 0.0:
        reason = (
            f"partially filled: participation rate {participation_rate:.2%} capped "
            f"quantity from {requested:.2f} to {filled:.2f}."
        )
    else:
        reason = (
            f"unfilled: participation rate {participation_rate:.2%} allowed at most "
            f"{max_fill:.2f} shares against a requested {requested:.2f}."
        )
    return VolumeLimitOutcome(
        order=order,
        requested_quantity=requested,
        max_fill_quantity=max_fill,
        filled_quantity=filled,
        unfilled_quantity=unfilled,
        policy=policy,
        reason=reason,
    )
