"""Corporate action processing for backtest positions (MVP 2 / SP 2.44).

Applies MVP 1's market-specific corporate-action rules (SP 1.34) and equity
entitlement calculation (SP 1.79 / 1.80) to a held backtest position. Each
event's action type is validated against the market's allowed types — Hong
Kong (供股/合股/要约) and United States (拆股/并购/分拆) rules are never mixed —
and the entitled quantity / cash amount comes from
:func:`harbor.core.equity.compute_entitlement`, so the backtest reuses the
same results as MVP 1.

Share-transforming actions (split, consolidation, rights issue, merger,
spin-off) replace the position quantity with the entitled quantity; cash-only
actions (dividend, tender offer) leave the quantity unchanged and produce a
cash amount that the orchestration layer (SP 2.47) routes to the ledger. The
event timing (which event applies to which position) is decided by the
orchestration layer; this module only transforms an already-entitled position.

Pure core logic: depends only on the domain types, the equity module and the
market registry; never touches storage or CLI code.
"""

from dataclasses import dataclass

from harbor.core.backtest_domain import Market, Position, to_market_target
from harbor.core.equity import EntitlementEvent, compute_entitlement
from harbor.core.market_registry import CorporateActionType


@dataclass(frozen=True)
class PositionAdjustment:
    """The effect of a corporate action on a position (SP 2.44)."""

    market: Market
    symbol: str
    action_id: str
    action_type: CorporateActionType
    old_quantity: float
    new_quantity: float
    cash_amount: float

    @property
    def shares_changed(self) -> bool:
        """Whether the action changed the position's share quantity."""
        return self.new_quantity != self.old_quantity

    def readable(self) -> str:
        """Render the adjustment as a human-readable summary."""
        lines = [
            f"corporate action {self.action_type.value} ({self.action_id}) "
            f"on {self.symbol}: {self.old_quantity:.2f} -> {self.new_quantity:.2f} shares"
        ]
        if self.cash_amount != 0.0:
            lines.append(f"  cash: {self.cash_amount:.2f}")
        return "\n".join(lines)


def apply_corporate_action(
    position: Position,
    event: EntitlementEvent,
) -> PositionAdjustment:
    """Apply a corporate action to an entitled position (SP 2.44).

    The entitled quantity and cash amount are computed by MVP 1's equity
    entitlement logic (reused via :func:`~harbor.core.equity.compute_entitlement`),
    and the event's action type is validated against the position's market.

    Args:
        position: The held position (assumed entitled to ``event``).
        event: The corporate action to apply.

    Returns:
        A :class:`PositionAdjustment` describing the new quantity and any cash
        entitlement.

    Raises:
        ValueError: If the event's action type is invalid for the position's
            market, the event lacks required terms, or the position quantity is
            negative.
    """
    market_target = to_market_target(position.market)
    entitled_quantity, cash_amount = compute_entitlement(
        market_target, position.symbol, position.quantity, event
    )
    if entitled_quantity > 0:
        new_quantity = entitled_quantity
    else:
        new_quantity = position.quantity
    return PositionAdjustment(
        market=position.market,
        symbol=position.symbol,
        action_id=event.action_id,
        action_type=event.action_type,
        old_quantity=position.quantity,
        new_quantity=new_quantity,
        cash_amount=cash_amount,
    )
