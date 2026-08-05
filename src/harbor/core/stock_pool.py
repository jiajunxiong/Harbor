"""Historical stock pool contract (MVP 2 / SP 2.10).

The stock pool is the universe of securities a strategy may trade, scoped to a
market and evaluated on a date. Every membership carries its inclusion
(effective) date, expiry date and source so its validity window is auditable.

A pool cannot prove from its own rows that no historical constituent was
omitted, so survivorship-bias risk is surfaced explicitly. When the source does
not guarantee historical constituents, when a membership lacks an inclusion
date, or when no membership is active on the date, the pool is marked
``survivorship_bias_risk=True`` with a human-readable reason rather than
silently treated as complete (SP 2.10).

This module is pure core logic: it depends only on the backtest domain types
and never touches storage or CLI code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Market


@dataclass(frozen=True)
class StockPoolMembership:
    """A security's validity window in a stock pool, with its source."""

    market: Market
    symbol: str
    effective_date: date | None
    expiry_date: date | None
    source: str


def is_active_on(membership: StockPoolMembership, as_of: date) -> bool:
    """Return whether a membership was valid on ``as_of``.

    A membership is active when its effective date is known and on or before
    ``as_of`` and it has not expired (expiry unknown or on/after ``as_of``).
    An unknown effective date means the membership window cannot be confirmed,
    so the security is not treated as active.
    """
    effective = membership.effective_date
    if effective is None or effective > as_of:
        return False
    expiry = membership.expiry_date
    return expiry is None or expiry >= as_of


@dataclass(frozen=True)
class StockPool:
    """The historical stock pool of a market evaluated on a date."""

    market: Market
    as_of: date
    source: str
    memberships: tuple[StockPoolMembership, ...]
    survivorship_bias_risk: bool
    risk_reason: str | None = None

    @property
    def symbols(self) -> tuple[str, ...]:
        """The symbols in the pool, sorted for deterministic ordering."""
        return tuple(sorted(membership.symbol for membership in self.memberships))


def evaluate_stock_pool(
    market: Market,
    as_of: date,
    memberships: Sequence[StockPoolMembership],
    source: str,
    *,
    historical_known: bool,
) -> StockPool:
    """Evaluate a stock pool on a date, marking survivorship-bias risk.

    Args:
        market: The market the pool belongs to.
        as_of: The date the pool is evaluated on.
        memberships: The full set of known memberships for the market.
        source: The source of the stock pool.
        historical_known: Whether the source is known to provide historical
            constituents, including delisted names. ``False`` marks the pool
            at risk, because the absence of delisted names cannot be proven
            from the pool's own rows.

    Returns:
        A :class:`StockPool` whose memberships are the securities active on
        ``as_of``. Risk is raised when any of the following holds (first match
        wins): the source does not guarantee historical constituents; no
        membership is active on the date; or some membership lacks an
        inclusion (effective) date.
    """
    active = tuple(membership for membership in memberships if is_active_on(membership, as_of))
    risk_reason: str | None = None
    if not historical_known:
        risk_reason = "source does not guarantee historical constituents"
    elif not active:
        risk_reason = "no memberships are active on the as-of date"
    elif any(membership.effective_date is None for membership in memberships):
        risk_reason = "some memberships lack an inclusion (effective) date"
    return StockPool(
        market=market,
        as_of=as_of,
        source=source,
        memberships=active,
        survivorship_bias_risk=risk_reason is not None,
        risk_reason=risk_reason,
    )
