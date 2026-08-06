"""Equity entitlement calculation for Harbor.

Corporate actions entitle shareholders who hold a position on the record date
to receive shares and/or cash. This module determines whether a position is
eligible for each corporate action (based on the record date) and computes the
resulting entitled quantity and cash amount.

The calculation is market-aware: every event's action type is validated against
the market's allowed corporate action types, so Hong Kong (供股/股息) and United
States (拆股/并购/分拆) rules are never mixed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from harbor.config import MarketTarget
from harbor.core.action_mapping import allowed_action_types
from harbor.core.adjustments import ActionTerms
from harbor.core.market_registry import CorporateActionType


@dataclass(frozen=True)
class EntitlementEvent:
    """A corporate action that may entitle a position."""

    action_id: str
    action_type: CorporateActionType
    terms: ActionTerms = ActionTerms()
    record_date: date | None = None
    ex_date: date | None = None


def _require_ratio(ratio: float | None) -> float:
    """Return a positive ratio, otherwise raise."""
    if ratio is None or ratio <= 0:
        raise ValueError("A positive ratio term is required.")
    return ratio


def _entitlement_for(
    action_type: CorporateActionType,
    terms: ActionTerms,
    quantity: float,
) -> tuple[float, float]:
    """Return the entitled quantity and cash amount for an event."""
    if quantity < 0:
        raise ValueError("Position quantity must be non-negative.")
    ratio = terms.ratio
    price = terms.price
    if action_type is CorporateActionType.SPLIT:
        return quantity * _require_ratio(ratio), 0.0
    if action_type is CorporateActionType.CONSOLIDATION:
        return quantity * _require_ratio(ratio), 0.0
    if action_type is CorporateActionType.RIGHTS_ISSUE:
        return quantity * _require_ratio(ratio), 0.0
    if action_type is CorporateActionType.MERGER:
        return quantity * _require_ratio(ratio), 0.0
    if action_type is CorporateActionType.SPIN_OFF:
        return quantity * _require_ratio(ratio), 0.0
    if action_type is CorporateActionType.DIVIDEND:
        if price is None:
            raise ValueError("dividend requires a price term (per-share amount).")
        return 0.0, quantity * price
    if action_type is CorporateActionType.TENDER_OFFER:
        if price is None:
            raise ValueError("tender_offer requires a price term.")
        return 0.0, quantity * price
    raise NotImplementedError(f"Unsupported corporate action type: {action_type}")


def _is_entitled(position_date: date, event: EntitlementEvent) -> bool:
    """Return whether a position held on ``position_date`` qualifies.

    A position qualifies when it was held on the event's record date; when no
    record date is known the ex-date is used as the reference.
    """
    reference = event.record_date if event.record_date is not None else event.ex_date
    return reference is not None and position_date <= reference


def compute_equity_entitlement(
    market: MarketTarget,
    symbol: str,
    position_date: date,
    quantity: float,
    events: Sequence[EntitlementEvent],
) -> list[dict[str, object]]:
    """Compute the equity entitlement for a position on a given date.

    Args:
        market: The market the position belongs to.
        symbol: The security symbol.
        position_date: The date of the position snapshot.
        quantity: The number of shares held in the snapshot.
        events: Corporate actions that may entitle the position.

    Returns:
        Rows with ``market``, ``symbol``, ``position_date``, ``action_id``,
        ``entitled_quantity``, ``cash_amount``, and ``processed_at`` keys, one
        per entitled event. Non-entitled events produce no row.

    Raises:
        ValueError: If an event's action type is invalid for the market, an
            event lacks required terms, or the quantity is negative.
    """
    allowed = allowed_action_types(market)
    rows: list[dict[str, object]] = []
    processed_at = datetime.now(timezone.utc)
    for event in events:
        if event.action_type not in allowed:
            raise ValueError(
                f"Corporate action type {event.action_type.value!r} is not supported for "
                f"{market.value}."
            )
        if not _is_entitled(position_date, event):
            continue
        entitled_quantity, cash_amount = _entitlement_for(event.action_type, event.terms, quantity)
        rows.append(
            {
                "market": market.value,
                "symbol": symbol,
                "position_date": position_date,
                "action_id": event.action_id,
                "entitled_quantity": entitled_quantity,
                "cash_amount": cash_amount,
                "processed_at": processed_at,
            }
        )
    return rows


def compute_entitlement(
    market: MarketTarget,
    symbol: str,
    quantity: float,
    event: EntitlementEvent,
) -> tuple[float, float]:
    """Return the entitled quantity and cash amount for one event (SP 2.44).

    Reuses the same market-allowance validation and entitlement math as
    :func:`compute_equity_entitlement`, without the snapshot-date check: the
    backtest corporate-action layer (SP 2.44) applies an event to a position it
    already knows was held at the record date, and the orchestration layer
    (SP 2.47) handles event timing.

    Args:
        market: The market the position belongs to.
        symbol: The security symbol.
        quantity: The number of shares held.
        event: The corporate action to apply.

    Returns:
        A tuple ``(entitled_quantity, cash_amount)``.

    Raises:
        ValueError: If the event's action type is invalid for the market, the
            event lacks required terms, or ``quantity`` is negative.
    """
    allowed = allowed_action_types(market)
    if event.action_type not in allowed:
        raise ValueError(
            f"Corporate action type {event.action_type.value!r} is not supported for "
            f"{market.value}."
        )
    return _entitlement_for(event.action_type, event.terms, quantity)
