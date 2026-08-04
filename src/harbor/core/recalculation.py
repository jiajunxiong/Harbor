"""Post-correction recalculation mechanism for Harbor.

Events that fail automatic processing are queued for manual review. Once an
operator corrects the underlying data, this module re-validates the corrected
events and, when they are all auto-processable, recomputes the adjusted price
factors and the equity entitlements for the affected symbol. The mechanism is
market-aware and reused for both Hong Kong (1.83) and United States (1.84).
"""

from collections.abc import Mapping, Sequence
from datetime import date

from harbor.config import MarketTarget
from harbor.core.adjustments import AdjustmentEvent, compute_adjustment_factors
from harbor.core.equity import EntitlementEvent, compute_equity_entitlement
from harbor.core.review_queue import classify_corporate_action


def _as_row(
    market: MarketTarget,
    symbol: str,
    action_id: str,
    event: AdjustmentEvent | EntitlementEvent,
) -> dict[str, object]:
    """Convert a corrected event into a raw corporate action row."""
    return {
        "market": market.value,
        "symbol": symbol,
        "action_id": action_id,
        "action_type": event.action_type.value,
        "ratio": event.terms.ratio,
        "price": event.terms.price,
    }


def recalculate(
    market: MarketTarget,
    symbol: str,
    trading_days: Sequence[date],
    close_prices: Mapping[date, float],
    adjustment_events: Sequence[AdjustmentEvent],
    entitlement_events: Sequence[EntitlementEvent],
    position_date: date | None = None,
    quantity: float | None = None,
) -> dict[str, object]:
    """Recalculate adjusted factors and equity entitlements after correction.

    Args:
        market: The market the symbol belongs to.
        symbol: The security symbol.
        trading_days: Ascending trading dates covered by the price series.
        close_prices: Closing price per trading date.
        adjustment_events: Corrected corporate actions used to recompute the
            adjusted price factors.
        entitlement_events: Corrected corporate actions used to recompute the
            equity entitlements.
        position_date: The position snapshot date, when equity is required.
        quantity: The number of shares held, when equity is required.

    Returns:
        A report with ``recalculated`` set to ``True`` and the recomputed
        ``adjusted_factors`` and ``equity_events`` rows, or ``False`` with the
        remaining ``review_items`` when any corrected event still cannot be
        processed automatically.
    """
    rows: list[dict[str, object]] = []
    for index, adjustment in enumerate(adjustment_events):
        rows.append(_as_row(market, symbol, f"{symbol}-adj-{index + 1}", adjustment))
    for entitlement in entitlement_events:
        rows.append(_as_row(market, symbol, entitlement.action_id, entitlement))

    review_items = [
        item
        for item in (classify_corporate_action(market, row) for row in rows)
        if item is not None
    ]
    if review_items:
        return {
            "market": market.value,
            "symbol": symbol,
            "recalculated": False,
            "review_items": review_items,
        }

    factors = compute_adjustment_factors(
        market, symbol, trading_days, close_prices, adjustment_events
    )
    equity_rows: list[dict[str, object]] = []
    if position_date is not None and quantity is not None:
        equity_rows = compute_equity_entitlement(
            market, symbol, position_date, quantity, entitlement_events
        )
    return {
        "market": market.value,
        "symbol": symbol,
        "recalculated": True,
        "adjusted_factors": factors,
        "equity_events": equity_rows,
    }
