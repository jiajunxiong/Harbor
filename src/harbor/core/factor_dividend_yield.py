"""Dividend yield factor (MVP 2 / SP 2.17).

Computes an annualized dividend yield from regular dividends only by default:
special dividends are excluded from the ranking numerator and tracked
separately so they can be surfaced without distorting the yield. The factor
consumes dividends already aligned to a decision date (SP 2.15) and re-filters
defensively so no ex-date after the decision date can enter the numerator.

Annualization scales a window dividend sum to a 365-day basis (``sum *
365 / lookback_days``); with the default 365-day lookback this is the standard
trailing-twelve-month (TTM) yield. A missing or non-positive price makes the
yield unavailable (``None``) rather than a fabricated number; a company with no
eligible dividends has a legitimate yield of ``0.0``.

Pure core logic: depends only on the backtest domain types and never touches
storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from harbor.core.backtest_interfaces import Dividend

#: Default trailing window (days) for the yield, i.e. trailing twelve months.
DEFAULT_LOOKBACK_DAYS = 365

#: Days used to annualize a window dividend sum.
_ANNUAL_DAYS = 365


def annualize_dividend_sum(total: float, lookback_days: int) -> float:
    """Scale a window dividend sum to a 365-day annualized basis.

    ``total * 365 / lookback_days``. With ``lookback_days == 365`` the sum is
    already on a trailing-twelve-month basis and is returned unchanged.

    Raises:
        ValueError: If ``lookback_days`` is not positive.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive.")
    return total * (_ANNUAL_DAYS / lookback_days)


def _in_window(dividend: Dividend, decision_date: date, lookback_days: int) -> bool:
    """Return whether the dividend's ex-date falls in the trailing window."""
    window_start = decision_date - timedelta(days=lookback_days)
    return window_start <= dividend.ex_date <= decision_date


@dataclass(frozen=True)
class DividendYieldResult:
    """The dividend yield factor outcome for one symbol on a decision date.

    ``value`` is the annualized yield (a fraction, e.g. ``0.03``), or ``None``
    when the latest price is missing or not positive. ``eligible_sum`` is the
    dividend sum included in the numerator and ``dividend_count`` how many
    dividends contributed. ``special_sum`` tracks special dividends in the
    window separately (SP 2.17): by default they are excluded from the
    numerator and therefore from ranking.
    """

    value: float | None
    eligible_sum: float
    special_sum: float
    dividend_count: int
    latest_price: float | None
    decision_date: date
    lookback_days: int
    include_special: bool


def dividend_yield_factor(
    dividends: tuple[Dividend, ...] | list[Dividend],
    latest_price: float | None,
    decision_date: date,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    include_special: bool = False,
) -> DividendYieldResult:
    """Compute the annualized dividend yield on a decision date (SP 2.17).

    Uses only dividends whose ex-date falls on or before ``decision_date`` and
    within the trailing ``lookback_days``. Regular dividends always contribute
    to the numerator; special dividends contribute only when ``include_special``
    is true (default: excluded from ranking but tracked in ``special_sum``).

    Args:
        dividends: Dividends aligned to the decision date (SP 2.15).
        latest_price: The most recent close known on or before the decision
            date, or ``None`` when unavailable.
        decision_date: The date the factor is evaluated on.
        lookback_days: Trailing window in calendar days used to annualize.
        include_special: Whether special dividends enter the numerator.

    Raises:
        ValueError: If ``lookback_days`` is not positive.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive.")
    eligible_sum = 0.0
    special_sum = 0.0
    dividend_count = 0
    for dividend in dividends:
        if not _in_window(dividend, decision_date, lookback_days):
            continue
        if dividend.is_special:
            special_sum += dividend.amount
            if include_special:
                eligible_sum += dividend.amount
                dividend_count += 1
        else:
            eligible_sum += dividend.amount
            dividend_count += 1
    value: float | None = None
    if latest_price is not None and latest_price > 0:
        value = annualize_dividend_sum(eligible_sum, lookback_days) / latest_price
    return DividendYieldResult(
        value=value,
        eligible_sum=eligible_sum,
        special_sum=special_sum,
        dividend_count=dividend_count,
        latest_price=latest_price,
        decision_date=decision_date,
        lookback_days=lookback_days,
        include_special=include_special,
    )
