"""Trade and turnover metrics (MVP 2 / SP 2.54).

Summarizes the execution trail of a run (SP 2.47 / SP 2.7): the number of
fills, the win rate of closed round trips, the average holding period,
one-sided turnover, cumulative costs, slippage and the unfilled (refused)
order statistics.

A round trip closes a previously-opened lot: buys open lots (FIFO per
market/symbol), sells close them, and each closed lot's P&L is
``(sell - buy) * qty - allocated fee``. The win rate is the share of closed
round trips with positive P&L; the average holding period is the mean number of
days a lot was held before closing. Turnover is one-sided: ``min(buy, sell)
value in the base currency / average portfolio value``.

Values expressed in the base currency are converted explicitly. A fill whose
currency differs from the base requires a positive FX rate — a missing rate
raises :class:`TradeStatsError` rather than assuming 1:1 (SP 2.12). Metrics
that need data the caller did not provide (e.g. turnover without portfolio
values) are ``None`` rather than fabricated.

Pure core logic: depends only on the domain types; never touches storage or
CLI code.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from harbor.core.backtest_domain import Currency, Fill, Market, OrderSide
from harbor.core.suspension import RefusedOrder


class TradeStatsError(ValueError):
    """Raised when a trade metric cannot be computed (SP 2.54)."""


@dataclass(frozen=True)
class RoundTrip:
    """A closed buy-then-sell round trip (SP 2.54)."""

    market: Market
    symbol: str
    currency: Currency
    buy_date: date
    sell_date: date
    quantity: float
    pnl: float
    holding_days: int

    @property
    def profitable(self) -> bool:
        """Whether the round trip closed with positive P&L."""
        return self.pnl > 0.0


@dataclass(frozen=True)
class TradeStats:
    """The consolidated trade and turnover metrics (SP 2.54)."""

    fill_count: int
    buy_count: int
    sell_count: int
    round_trip_count: int
    win_count: int
    win_rate: float | None
    average_holding_days: float | None
    turnover: float | None
    total_fees_base: float
    slippage_cost_base: float
    unfilled_count: int
    refused_reasons: MappingProxyType[str, int]

    def readable(self) -> str:
        """Render the trade metrics as a human-readable summary."""
        lines = [
            f"Trades: {self.fill_count} fills ({self.buy_count} buys / {self.sell_count} sells)",
            f"  round trips: {self.round_trip_count}",
        ]
        if self.win_rate is not None:
            lines.append(f"  win rate: {self.win_rate:.4%}")
        if self.average_holding_days is not None:
            lines.append(f"  average holding: {self.average_holding_days:.2f} days")
        if self.turnover is not None:
            lines.append(f"  one-sided turnover: {self.turnover:.4f}")
        lines.append(f"  total fees (base): {self.total_fees_base:.2f}")
        lines.append(f"  slippage cost (base): {self.slippage_cost_base:.2f}")
        lines.append(f"  unfilled orders: {self.unfilled_count}")
        return "\n".join(lines)


@dataclass
class _OpenLot:
    """An open (bought, not yet sold) lot (internal, FIFO tracking)."""

    quantity: float
    price: float
    buy_date: date
    currency: Currency


def _round_trips(fills: Sequence[Fill]) -> tuple[RoundTrip, ...]:
    """Pair buys and sells into FIFO round trips per market/symbol."""
    lots: dict[tuple[Market, str], list[_OpenLot]] = {}
    trips: list[RoundTrip] = []
    for fill in fills:
        key = (fill.market, fill.symbol)
        if fill.side is OrderSide.BUY:
            lots.setdefault(key, []).append(
                _OpenLot(fill.quantity, fill.price, fill.trade_date, fill.currency)
            )
            continue
        remaining = fill.quantity
        while remaining > 1e-9 and lots.get(key):
            lot = lots[key][0]
            closed = min(lot.quantity, remaining)
            fee_share = fill.fee * (closed / fill.quantity)
            pnl = (fill.price - lot.price) * closed - fee_share
            trips.append(
                RoundTrip(
                    market=fill.market,
                    symbol=fill.symbol,
                    currency=fill.currency,
                    buy_date=lot.buy_date,
                    sell_date=fill.trade_date,
                    quantity=closed,
                    pnl=pnl,
                    holding_days=(fill.trade_date - lot.buy_date).days,
                )
            )
            lot.quantity -= closed
            remaining -= closed
            if lot.quantity <= 1e-9:
                lots[key].pop(0)
    return tuple(trips)


def _to_base(
    currency: Currency,
    amount: float,
    day: date,
    base_currency: Currency,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> float:
    """Convert ``amount`` into the base currency, refusing a missing rate."""
    if currency is base_currency:
        return amount
    rate = fx_rate(currency, base_currency, day)
    if rate is None or rate <= 0:
        raise TradeStatsError(
            f"Missing FX rate to convert {currency.value} to {base_currency.value} "
            f"on {day.isoformat()}; refusing to assume 1:1."
        )
    return amount * rate


def compute_trade_stats(
    fills: Sequence[Fill],
    *,
    refused: Sequence[RefusedOrder] = (),
    net_values: Sequence[float] = (),
    base_currency: Currency,
    fx_rate: Callable[[Currency, Currency, date], float | None],
    slippage_bps: float = 0.0,
) -> TradeStats:
    """Compute the trade and turnover metrics for a run (SP 2.54).

    Args:
        fills: The executed fills in trade-date order (SP 2.47).
        refused: The refused (unfilled) orders (SP 2.41/2.7).
        net_values: The daily total net-value series for turnover (SP 2.45).
        base_currency: The currency all money metrics are expressed in.
        fx_rate: Returns base units per one unit of the source currency for a
            day, or ``None`` when unavailable (SP 2.12).
        slippage_bps: The configured slippage (SP 2.38); slippage cost is
            ``qty * price * bps / 10000`` per US fill.

    Returns:
        A :class:`TradeStats` with the trade and turnover metrics.

    Raises:
        TradeStatsError: If a fill's currency differs from the base currency
            and its FX rate is missing.
    """
    trips = _round_trips(fills)
    buy_count = sum(1 for fill in fills if fill.side is OrderSide.BUY)
    sell_count = len(fills) - buy_count

    buy_base = 0.0
    sell_base = 0.0
    total_fees_base = 0.0
    slippage_base = 0.0
    for fill in fills:
        notional_base = _to_base(
            fill.currency, fill.notional, fill.trade_date, base_currency, fx_rate
        )
        fee_base = _to_base(fill.currency, fill.fee, fill.trade_date, base_currency, fx_rate)
        if fill.side is OrderSide.BUY:
            buy_base += notional_base
        else:
            sell_base += notional_base
        total_fees_base += fee_base
        if fill.market is Market.US and slippage_bps > 0:
            slippage_base += _to_base(
                fill.currency,
                fill.quantity * fill.price * slippage_bps / 10_000.0,
                fill.trade_date,
                base_currency,
                fx_rate,
            )

    win_count = sum(1 for trip in trips if trip.profitable)
    win_rate = (win_count / len(trips)) if trips else None
    average_holding = sum(trip.holding_days for trip in trips) / len(trips) if trips else None

    turnover: float | None = None
    if net_values:
        average_value = sum(net_values) / len(net_values)
        if average_value > 0:
            turnover = min(buy_base, sell_base) / average_value

    reasons: dict[str, int] = {}
    for refused_order in refused:
        reasons[refused_order.reason] = reasons.get(refused_order.reason, 0) + 1

    return TradeStats(
        fill_count=len(fills),
        buy_count=buy_count,
        sell_count=sell_count,
        round_trip_count=len(trips),
        win_count=win_count,
        win_rate=win_rate,
        average_holding_days=average_holding,
        turnover=turnover,
        total_fees_base=total_fees_base,
        slippage_cost_base=slippage_base,
        unfilled_count=len(refused),
        refused_reasons=MappingProxyType(reasons),
    )
