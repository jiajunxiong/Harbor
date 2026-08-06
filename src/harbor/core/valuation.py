"""Portfolio net-value valuation (MVP 2 / SP 2.45).

Computes, for a trading day, the cash, the position market values, the
cumulative realized fees and the net value in the base currency. Position
prices come from the SP 2.41 suspension valuation (which carries the
missing-price rule: a carried-forward last close is flagged and warned, so the
缺价规则可追溯); FX conversion is explicit — a missing rate refuses the
valuation rather than assuming 1:1 (MVP 2 acceptance criteria).

The net value is cash + position market value, both expressed in the base
currency. Realized FX translation P&L (SP 2.42) is already reflected inside the
cash balances, so it is reported on the snapshot but never added again.

Pure core logic: depends only on the domain types, the ledger and the
suspension valuation; never touches storage or CLI code.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import CashBalance, Currency, Market, NetValue, Position
from harbor.core.fx import FxConversionError
from harbor.core.ledger import Ledger
from harbor.core.suspension import PositionValuation, SuspensionWarning


@dataclass(frozen=True)
class PositionValue:
    """Market value of one position in the base currency (SP 2.45).

    ``carried_forward`` and ``warning`` propagate the SP 2.41 missing-price rule
    so a stale valuation is always traceable.
    """

    market: Market
    symbol: str
    quantity: float
    price: float
    currency: Currency
    fx_rate: float
    market_value_quote: float
    market_value_base: float
    carried_forward: bool
    warning: SuspensionWarning | None

    def readable(self) -> str:
        """Render the position value as a human-readable summary."""
        tag = " (carried forward)" if self.carried_forward else ""
        return (
            f"{self.symbol}: {self.quantity:.2f} x {self.price:.4f} "
            f"{self.currency.value} = {self.market_value_base:.2f} "
            f"base{tag}"
        )


@dataclass(frozen=True)
class DailyValuation:
    """Net-value snapshot for one trading day (SP 2.45)."""

    as_of: date
    base_currency: Currency
    cash: tuple[CashBalance, ...]
    position_values: tuple[PositionValue, ...]
    realized_fees: tuple[CashBalance, ...]
    fx_pnl: float
    net_value: NetValue

    def readable(self) -> str:
        """Render the daily valuation as a human-readable summary."""
        lines = [
            f"net value on {self.as_of.isoformat()} "
            f"({self.base_currency.value}): {self.net_value.total_value:.2f}"
        ]
        for entry in self.cash:
            lines.append(f"  cash {entry.currency.value}: {entry.amount:.2f}")
        lines.append(f"  securities: {self.net_value.securities_value:.2f}")
        lines.append(f"  cumulative fees: {self.net_value.fees_paid:.2f}")
        lines.append(f"  fx pnl (base): {self.fx_pnl:.2f}")
        return "\n".join(lines)


def _base_rate(
    from_currency: Currency,
    base_currency: Currency,
    as_of: date,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> float:
    """Return base units per one unit of ``from_currency`` (SP 2.45).

    Refuses a missing or non-positive rate rather than assuming 1:1 (MVP 2
    acceptance criteria).
    """
    if from_currency is base_currency:
        return 1.0
    rate = fx_rate(from_currency, base_currency, as_of)
    if rate is None or rate <= 0:
        raise FxConversionError(
            f"Missing FX rate to value {from_currency.value} in "
            f"{base_currency.value} on {as_of.isoformat()}; refusing to assume 1:1."
        )
    return rate


def value_portfolio(
    *,
    as_of: date,
    base_currency: Currency,
    ledger: Ledger,
    positions: Sequence[Position],
    valuations: Mapping[tuple[Market, str], PositionValuation],
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> DailyValuation:
    """Value the portfolio on ``as_of`` (SP 2.45).

    Args:
        as_of: The valuation day.
        base_currency: The reporting (benchmark) currency.
        ledger: The multi-currency cash ledger (SP 2.42).
        positions: The held positions.
        valuations: The SP 2.41 valuation per ``(market, symbol)``; every
            position must have one, so a missing price is never fabricated.
        fx_rate: A callable returning base units per one unit of the source
            currency for a day (``None`` when unknown).

    Returns:
        A :class:`DailyValuation` with per-position values and the base-currency
        net value.

    Raises:
        ValueError: If a position has no valuation.
        FxConversionError: If a foreign currency (position or cash) lacks a
            positive FX rate.
    """
    position_values: list[PositionValue] = []
    for position in positions:
        key = (position.market, position.symbol)
        valuation = valuations.get(key)
        if valuation is None:
            raise ValueError(
                f"Missing valuation for {position.symbol} on {as_of.isoformat()}; "
                "cannot value the position without fabricating a price."
            )
        rate = _base_rate(position.currency, base_currency, as_of, fx_rate)
        market_value_quote = position.quantity * valuation.price
        market_value_base = market_value_quote * rate
        position_values.append(
            PositionValue(
                market=position.market,
                symbol=position.symbol,
                quantity=position.quantity,
                price=valuation.price,
                currency=position.currency,
                fx_rate=rate,
                market_value_quote=market_value_quote,
                market_value_base=market_value_base,
                carried_forward=valuation.carried_forward,
                warning=valuation.warning,
            )
        )
    position_values.sort(key=lambda pv: (pv.market.value, pv.symbol))

    cash_base = 0.0
    for entry in ledger.cash:
        rate = _base_rate(entry.currency, base_currency, as_of, fx_rate)
        cash_base += entry.amount * rate

    positions_base = sum(pv.market_value_base for pv in position_values)

    fees_base = 0.0
    for entry in ledger.realized_fees:
        rate = _base_rate(entry.currency, base_currency, as_of, fx_rate)
        fees_base += entry.amount * rate

    net_value = NetValue(
        as_of_date=as_of,
        currency=base_currency,
        cash=cash_base,
        securities_value=positions_base,
        fees_paid=fees_base,
    )
    return DailyValuation(
        as_of=as_of,
        base_currency=base_currency,
        cash=ledger.cash,
        position_values=tuple(position_values),
        realized_fees=ledger.realized_fees,
        fx_pnl=ledger.fx_pnl,
        net_value=net_value,
    )
