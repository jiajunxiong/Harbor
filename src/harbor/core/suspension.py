"""Suspension and untradeable-symbol handling (MVP 2 / SP 2.41).

A symbol is treated as suspended / untradeable on a day when it has no quote
for that day (no trading) or a zero-volume quote. Suspended symbols are
forbidden from producing new fills: an order for a suspended symbol is refused
with a human-readable reason that feeds the rejected-trade trail (SP 2.7).

Existing positions are not marked up or down on suspension: they are valued at
the last available close, the explicit valuation rule (SP 2.41, configured via
SP 2.4 :class:`~harbor.core.backtest_config.SuspensionConfig`), and every
carry-forward valuation produces a :class:`SuspensionWarning` so the staleness
is visible in the result / audit trail.

Pure core logic: depends only on the domain types and the configuration; never
touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_config import SuspensionConfig, SuspensionValuation
from harbor.core.backtest_domain import Market, Order
from harbor.core.backtest_interfaces import DailyQuote


def is_tradeable(quote: DailyQuote | None) -> bool:
    """Whether a symbol can produce a fill on a day (SP 2.41).

    A symbol is tradeable when it has a quote for the day with positive volume.
    A missing quote (no trading) or a zero-volume quote means the symbol is
    suspended or otherwise not trading, so new fills are forbidden.
    """
    return quote is not None and quote.volume > 0


@dataclass(frozen=True)
class RefusedOrder:
    """An order refused because its symbol is suspended / untradeable (SP 2.41).

    The record keeps the originating order and a human-readable ``reason`` for
    the rejected-trade trail (SP 2.7).
    """

    order: Order
    day: date
    reason: str

    def readable(self) -> str:
        """Render the refusal as a human-readable summary."""
        return (
            f"refused {self.order.side.value} {self.order.symbol} on "
            f"{self.day.isoformat()}: {self.reason}"
        )


def refuse_order(
    *,
    order: Order,
    day: date,
    quote: DailyQuote | None,
) -> RefusedOrder | None:
    """Refuse an order when its symbol is not tradeable on ``day`` (SP 2.41).

    Args:
        order: The order to fill.
        day: The intended fill day.
        quote: The quote for that day (``None`` when the symbol did not trade).

    Returns:
        A :class:`RefusedOrder` with a reason when the symbol is suspended, or
        ``None`` when the symbol is tradeable.
    """
    if is_tradeable(quote):
        return None
    if quote is None:
        reason = f"no quote on {day.isoformat()}; symbol suspended or untradeable."
    else:
        reason = f"zero volume on {day.isoformat()}; symbol suspended or untradeable."
    return RefusedOrder(order=order, day=day, reason=reason)


@dataclass(frozen=True)
class SuspensionWarning:
    """A warning raised by a suspension or a carry-forward valuation (SP 2.41)."""

    market: Market
    symbol: str
    day: date
    message: str

    def readable(self) -> str:
        """Render the warning as a human-readable summary."""
        return f"[{self.market.value}] {self.symbol} on {self.day.isoformat()}: {self.message}"


@dataclass(frozen=True)
class PositionValuation:
    """The price used to value a position on a day (SP 2.41).

    ``carried_forward`` is true when the day had no quote and the position was
    valued at the last available close; that case carries a
    :class:`SuspensionWarning` (unless warnings are disabled in the
    configuration).
    """

    market: Market
    symbol: str
    price: float
    carried_forward: bool
    day: date
    warning: SuspensionWarning | None

    def readable(self) -> str:
        """Render the valuation as a human-readable summary."""
        tag = "last close (carried forward)" if self.carried_forward else "close"
        summary = f"valuation of {self.symbol} on {self.day.isoformat()}: {self.price:.4f} ({tag})"
        if self.warning is not None:
            summary += f"\n  warning: {self.warning.message}"
        return summary


def position_valuation_price(
    *,
    market: Market,
    symbol: str,
    day: date,
    quote: DailyQuote | None,
    last_quote: DailyQuote | None,
    config: SuspensionConfig | None = None,
) -> PositionValuation:
    """Value a position on ``day`` (SP 2.41).

    Uses the day's close when a quote exists; otherwise carries the last
    available close forward (the ``LAST_PRICE`` rule) and produces a warning.
    Raises when no price is available at all, so valuation never silently
    fabricates a price.

    Args:
        market: The position's market.
        symbol: The position's symbol.
        day: The valuation day.
        quote: The quote for ``day`` (``None`` when the symbol did not trade).
        last_quote: The most recent quote before ``day``, if any.
        config: The suspension configuration (SP 2.4).

    Raises:
        ValueError: If there is neither a quote for ``day`` nor a last
            available quote.
    """
    if config is None:
        config = SuspensionConfig()
    if quote is not None:
        return PositionValuation(
            market=market,
            symbol=symbol,
            price=quote.close,
            carried_forward=False,
            day=day,
            warning=None,
        )
    if config.valuation is SuspensionValuation.LAST_PRICE and last_quote is not None:
        message = (
            f"{symbol} has no quote on {day.isoformat()}; position valued at the "
            f"last available close {last_quote.close:.4f} on "
            f"{last_quote.day.isoformat()}."
        )
        warning = None
        if config.warn:
            warning = SuspensionWarning(market=market, symbol=symbol, day=day, message=message)
        return PositionValuation(
            market=market,
            symbol=symbol,
            price=last_quote.close,
            carried_forward=True,
            day=day,
            warning=warning,
        )
    raise ValueError(
        f"No price available to value {symbol} on {day.isoformat()}: "
        "no quote and no last available quote."
    )
