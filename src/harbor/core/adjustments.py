"""Adjusted price factor calculation for Harbor.

Corporate actions change the number of shares outstanding and/or the price of
a security. To compare prices across time we compute, per trading day, a daily
factor (the price adjustment caused by an event with that ex-date) and a
cumulative factor (the product of all future events' daily factors), so that a
historical price multiplied by its cumulative factor is expressed on the same
basis as the most recent price.

The calculation is market-aware: every event's action type is validated against
the market's allowed corporate action types before any factor is computed, so
Hong Kong (供股/合股/股息) and United States (拆股/并购/分拆/股息) rules are
never mixed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from harbor.config import MarketTarget
from harbor.core.action_mapping import allowed_action_types
from harbor.core.market_registry import CorporateActionType

_REFERENCE_ACTIONS = frozenset({CorporateActionType.RIGHTS_ISSUE, CorporateActionType.DIVIDEND})


@dataclass(frozen=True)
class ActionTerms:
    """Numeric terms of a corporate action."""

    ratio: float | None = None
    price: float | None = None


@dataclass(frozen=True)
class AdjustmentEvent:
    """A corporate action relevant to adjusted price factors."""

    ex_date: date
    action_type: CorporateActionType
    terms: ActionTerms = ActionTerms()


def _inverse_ratio(ratio: float | None) -> float:
    """Return the price factor for a share-ratio event."""
    if ratio is None or ratio <= 0:
        raise ValueError("A positive ratio term is required.")
    return 1.0 / ratio


def _require_reference(reference_price: float | None) -> float:
    """Return a positive reference price, otherwise raise."""
    if reference_price is None or reference_price <= 0:
        raise ValueError("A positive reference price is required.")
    return reference_price


def _rights_issue_factor(
    reference_price: float | None,
    ratio: float,
    price: float,
) -> float:
    """Return the theoretical ex-rights price ratio for a subscription."""
    reference = _require_reference(reference_price)
    return (reference + price * ratio) / (reference * (1.0 + ratio))


def _cash_dividend_factor(reference_price: float | None, amount: float) -> float:
    """Return the ex-dividend price ratio for a cash payout."""
    reference = _require_reference(reference_price)
    if amount < 0:
        raise ValueError("The dividend amount must be non-negative.")
    factor = (reference - amount) / reference
    if factor <= 0:
        raise ValueError("Dividend amount must be below the reference price.")
    return factor


def daily_factor_for(
    action_type: CorporateActionType,
    terms: ActionTerms,
    reference_price: float | None = None,
) -> float:
    """Return the price adjustment factor for a single corporate action.

    The factor is the ratio by which a pre-event price must be multiplied so it
    is comparable to the post-event price.

    Args:
        action_type: The canonical corporate action type.
        terms: The numeric terms of the action.
        reference_price: The closing price before the ex-date, required by
            events whose factor depends on the prevailing price.

    Returns:
        The daily price adjustment factor.

    Raises:
        ValueError: If required terms or a reference price are missing.
    """
    ratio = terms.ratio
    price = terms.price
    if action_type is CorporateActionType.SPLIT:
        return _inverse_ratio(ratio)
    if action_type is CorporateActionType.CONSOLIDATION:
        return _inverse_ratio(ratio)
    if action_type is CorporateActionType.RIGHTS_ISSUE:
        if ratio is None:
            raise ValueError("rights_issue requires a ratio term.")
        if price is None:
            raise ValueError("rights_issue requires a price term.")
        return _rights_issue_factor(reference_price, ratio, price)
    if action_type is CorporateActionType.DIVIDEND:
        if price is None:
            raise ValueError("dividend requires a price term (per-share amount).")
        return _cash_dividend_factor(reference_price, price)
    if action_type is CorporateActionType.MERGER:
        return _inverse_ratio(ratio)
    if action_type is CorporateActionType.SPIN_OFF:
        if ratio is None:
            raise ValueError("spin_off requires a ratio term.")
        return 1.0 / (1.0 + ratio)
    if action_type is CorporateActionType.TENDER_OFFER:
        return 1.0
    raise NotImplementedError(f"Unsupported corporate action type: {action_type}")


def _reference_close(
    trading_days: Sequence[date],
    close_prices: Mapping[date, float],
    ex_date: date,
) -> float | None:
    """Return the closing price on the last trading day before an ex-date."""
    for day in reversed(trading_days):
        if day < ex_date:
            return close_prices.get(day)
    return None


def _next_trading_day(trading_days: Sequence[date], event_date: date) -> date | None:
    """Return the first trading day at or after an event date."""
    for day in trading_days:
        if day >= event_date:
            return day
    return None


def compute_adjustment_factors(
    market: MarketTarget,
    symbol: str,
    trading_days: Sequence[date],
    close_prices: Mapping[date, float],
    events: Sequence[AdjustmentEvent],
) -> list[dict[str, object]]:
    """Compute daily and cumulative adjustment factors for a symbol.

    Args:
        market: The market the symbol belongs to.
        symbol: The security symbol.
        trading_days: Ascending trading dates covered by the price series.
        close_prices: Closing price per trading date.
        events: Corporate actions whose ex-dates fall within the price window.

    Returns:
        Rows with ``market``, ``symbol``, ``date``, ``daily_factor``, and
        ``cumulative_factor`` keys, one per trading day in ascending order.

    Raises:
        ValueError: If an event's action type is invalid for the market, an
            event lacks required terms, or a reference price is unavailable.
    """
    allowed = allowed_action_types(market)
    for event in events:
        if event.action_type not in allowed:
            raise ValueError(
                f"Corporate action type {event.action_type.value!r} is not supported for "
                f"{market.value}."
            )
    if not trading_days:
        return []
    first_day = min(trading_days)
    last_day = max(trading_days)

    daily_factors: dict[date, float] = {}
    effective_factors: dict[date, float] = {}
    for event in events:
        if event.ex_date > last_day:
            continue
        reference_price = None
        if event.action_type in _REFERENCE_ACTIONS:
            reference_price = _reference_close(trading_days, close_prices, event.ex_date)
        factor = daily_factor_for(event.action_type, event.terms, reference_price)
        daily_factors[event.ex_date] = daily_factors.get(event.ex_date, 1.0) * factor
        if first_day <= event.ex_date <= last_day:
            target = _next_trading_day(trading_days, event.ex_date)
            if target is not None:
                effective_factors[target] = effective_factors.get(target, 1.0) * factor

    sorted_event_dates = sorted(daily_factors)
    cumulative = 1.0
    for event_date in sorted_event_dates:
        cumulative *= daily_factors[event_date]

    rows: list[dict[str, object]] = []
    event_index = 0
    for day in trading_days:
        while event_index < len(sorted_event_dates) and sorted_event_dates[event_index] <= day:
            cumulative /= daily_factors[sorted_event_dates[event_index]]
            event_index += 1
        rows.append(
            {
                "market": market.value,
                "symbol": symbol,
                "date": day,
                "daily_factor": effective_factors.get(day, 1.0),
                "cumulative_factor": cumulative,
            }
        )
    return rows
