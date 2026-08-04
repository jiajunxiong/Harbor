"""Abnormal corporate action review queue for Harbor.

Events that the automatic pipeline cannot process — an unrecognized action
type, a type that is not allowed for the market, missing required terms, or a
market mismatch — are queued as review items and rendered into a JSON report so
they can be remediated manually. The queue is market-aware: every event is
classified against the target market's allowed action types.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from harbor.config import MarketTarget
from harbor.core.action_mapping import resolve_action_type, validate_action_type
from harbor.core.market_registry import CorporateActionType

_RATIO_ACTIONS = frozenset(
    {
        CorporateActionType.SPLIT,
        CorporateActionType.CONSOLIDATION,
        CorporateActionType.RIGHTS_ISSUE,
        CorporateActionType.MERGER,
        CorporateActionType.SPIN_OFF,
    }
)
_PRICE_ACTIONS = frozenset({CorporateActionType.DIVIDEND, CorporateActionType.TENDER_OFFER})


def _term_value(row: Mapping[str, Any], key: str) -> float | None:
    """Return a row term coerced to a float, or ``None`` when absent/invalid."""
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _missing_terms(
    action_type: CorporateActionType,
    row: Mapping[str, Any],
) -> list[str]:
    """Return the term names an action requires but the row lacks."""
    missing: list[str] = []
    ratio = _term_value(row, "ratio")
    if action_type in _RATIO_ACTIONS and (ratio is None or ratio <= 0):
        missing.append("ratio")
    price = _term_value(row, "price")
    if action_type is CorporateActionType.RIGHTS_ISSUE and price is None:
        missing.append("price")
    if action_type in _PRICE_ACTIONS and price is None:
        missing.append("price")
    return missing


def classify_corporate_action(
    market: MarketTarget,
    row: Mapping[str, Any],
) -> dict[str, object] | None:
    """Return a review item when an event cannot be auto-processed.

    Args:
        market: The market the event is expected to belong to.
        row: A raw corporate action row.

    Returns:
        A JSON-serializable review item describing the problem, or ``None``
        when the event can be processed automatically.
    """
    base: dict[str, object] = {
        "market": market.value,
        "symbol": str(row.get("symbol", "")),
        "action_id": str(row.get("action_id", "")),
        "action_type": str(row.get("action_type", "")),
    }
    if row.get("market") not in (None, market.value):
        return {**base, "reason": "market_mismatch", "details": dict(row)}

    raw_type = str(row.get("action_type", ""))
    try:
        action_type = resolve_action_type(raw_type)
    except ValueError as error:
        details: dict[str, object] = {"error": str(error), **dict(row)}
        return {**base, "reason": "unknown_action_type", "details": details}
    try:
        validate_action_type(market, action_type)
    except ValueError as error:
        details = {"error": str(error), **dict(row)}
        return {**base, "reason": "action_type_not_supported", "details": details}

    missing = _missing_terms(action_type, row)
    if missing:
        details = {"missing": missing, **dict(row)}
        return {**base, "reason": "missing_terms", "details": details}
    return None


def build_review_report(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Return the JSON report of events that cannot be auto-processed."""
    items: list[dict[str, object]] = []
    for row in rows:
        item = classify_corporate_action(market, row)
        if item is not None:
            items.append(item)
    return {
        "market": market.value,
        "total_events": len(rows),
        "review_count": len(items),
        "items": items,
    }


def render_review_report(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> str:
    """Render the abnormal-event review report as a JSON document."""
    return json.dumps(
        build_review_report(market, rows),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
