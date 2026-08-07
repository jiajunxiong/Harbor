"""Attribution basics (MVP 2 / SP 2.57).

Decomposes the daily net-value change (SP 2.45) into five research buckets:

- price return (价格收益): mark-to-market of held positions, computed as the
  securities-value change net of the day's buy/sell principal;
- dividends (股息): cash dividends credited on the day (SP 2.43);
- corporate actions (企业行动): cash credited by cash-settling actions such as
  tender offers (SP 2.44); quantity-only actions (splits, consolidations) are
  treated as value-preserving and contribute zero;
- trading costs (交易成本): commissions and fees paid on the day, negative;
- FX impact (FX 影响): the cash translation P&L tracked by the ledger
  (SP 2.42); the FX component of position values is reported inside price
  return.

The buckets sum to the net-value change, so attribution reconciles with the
accounting (对账): ``price_return + dividends + corporate_actions +
trading_costs + fx_impact == net_value_change`` (within float tolerance).
Slippage embedded in fill prices is reported inside price return, not as a
separate cost.

The analysis is research-only (不构成投资建议). A missing FX rate, an empty
result series, a non-positive net value, a non-positive initial capital, or a
series that is not in strictly ascending date order raises
:class:`AttributionError` rather than fabricating a number (the
never-assume/never-fabricate rule).

The baseline (the day before the first result) must be cash-only: its net value
is ``initial_capital`` and it carries no positions, fees or FX P&L. This
matches the SP 2.51 runner, which deposits the initial capital before the first
trading day.

Pure core logic: depends only on the backtest domain, valuation and runner
types; never touches storage or CLI code.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Currency, OrderSide
from harbor.core.backtest_runner import DailyResult


class AttributionError(ValueError):
    """Raised when attribution cannot be computed (SP 2.57)."""


@dataclass(frozen=True)
class DailyAttribution:
    """The attribution of one day's net-value change (SP 2.57)."""

    as_of: date
    previous_value: float
    net_value: float
    net_value_change: float
    price_return: float
    dividends: float
    corporate_actions: float
    trading_costs: float
    fx_impact: float
    gap: float

    def reconciled(self, tolerance: float = 1e-6) -> bool:
        """Whether the buckets sum to the net-value change within tolerance."""
        return abs(self.gap) <= tolerance

    def readable(self) -> str:
        """Render the day's attribution as a research summary."""
        return (
            f"{self.as_of.isoformat()}: change {self.net_value_change:+.2f} = "
            f"price {self.price_return:+.2f} + dividends {self.dividends:+.2f} + "
            f"corporate {self.corporate_actions:+.2f} + costs {self.trading_costs:+.2f} "
            f"+ fx {self.fx_impact:+.2f} (gap {self.gap:.2e})"
        )


@dataclass(frozen=True)
class AttributionReport:
    """The full per-day attribution for a run (SP 2.57)."""

    base_currency: Currency
    initial_capital: float
    tolerance: float
    days: tuple[DailyAttribution, ...]

    @property
    def total_net_value_change(self) -> float:
        """Total net-value change over the run."""
        return sum(day.net_value_change for day in self.days)

    @property
    def total_price_return(self) -> float:
        """Total price (mark-to-market) return over the run."""
        return sum(day.price_return for day in self.days)

    @property
    def total_dividends(self) -> float:
        """Total dividends attributed over the run."""
        return sum(day.dividends for day in self.days)

    @property
    def total_corporate_actions(self) -> float:
        """Total corporate-action cash attributed over the run."""
        return sum(day.corporate_actions for day in self.days)

    @property
    def total_trading_costs(self) -> float:
        """Total trading costs (negative) over the run."""
        return sum(day.trading_costs for day in self.days)

    @property
    def total_fx_impact(self) -> float:
        """Total cash FX translation P&L over the run."""
        return sum(day.fx_impact for day in self.days)

    @property
    def total_gap(self) -> float:
        """Float-level reconciliation residual across the run."""
        return sum(day.gap for day in self.days)

    @property
    def reconciled(self) -> bool:
        """Whether every day's buckets sum to its net-value change."""
        return all(day.reconciled(self.tolerance) for day in self.days)

    def readable(self) -> str:
        """Render the attribution report as a research summary."""
        if not self.days:
            return "No days to attribute."
        lines = [
            f"Attribution {self.days[0].as_of.isoformat()} -> "
            f"{self.days[-1].as_of.isoformat()} "
            f"(base {self.base_currency.value}, initial {self.initial_capital:.2f}):",
            f"  net value change:  {self.total_net_value_change:+.2f}",
            f"  price return:      {self.total_price_return:+.2f}",
            f"  dividends:         {self.total_dividends:+.2f}",
            f"  corporate actions: {self.total_corporate_actions:+.2f}",
            f"  trading costs:     {self.total_trading_costs:+.2f}",
            f"  fx impact:         {self.total_fx_impact:+.2f}",
            f"  reconciled: {self.reconciled} (gap {self.total_gap:.2e})",
        ]
        return "\n".join(lines)


def _base_rate(
    from_currency: Currency,
    base_currency: Currency,
    as_of: date,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> float:
    """Return base units per one unit of ``from_currency`` (SP 2.57)."""
    if from_currency is base_currency:
        return 1.0
    rate = fx_rate(from_currency, base_currency, as_of)
    if rate is None or rate <= 0:
        raise AttributionError(
            f"Missing FX rate to value {from_currency.value} in "
            f"{base_currency.value} on {as_of.isoformat()}; refusing to assume 1:1."
        )
    return rate


def _day_dividends(
    result: DailyResult,
    base_currency: Currency,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> float:
    """Return the base value of the day's cash dividends (SP 2.57)."""
    total = 0.0
    for dividend in result.dividends:
        rate = _base_rate(dividend.currency, base_currency, result.as_of, fx_rate)
        total += dividend.gross_amount * rate
    return total


def _day_corporate_cash(result: DailyResult) -> float:
    """Return the base value of cash-settling corporate actions (SP 2.57).

    The cash amount is credited in the position's quote currency, so it is
    converted with the position's own valuation FX rate; quantity-only actions
    (splits, consolidations) are value-preserving and contribute zero.
    """
    total = 0.0
    by_symbol = {
        (position.market, position.symbol): position
        for position in result.valuation.position_values
    }
    for adjustment in result.adjustments:
        if adjustment.cash_amount == 0.0:
            continue
        position = by_symbol.get((adjustment.market, adjustment.symbol))
        if position is None:
            raise AttributionError(
                f"Cannot attribute corporate action {adjustment.action_id} cash on "
                f"{result.as_of.isoformat()}: position {adjustment.symbol} is not held "
                "at the day end; refusing to assume a currency."
            )
        total += adjustment.cash_amount * position.fx_rate
    return total


def _net_buy(
    result: DailyResult,
    base_currency: Currency,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> float:
    """Return the day's buy minus sell principal in the base currency."""
    net = 0.0
    for fill in result.fills:
        rate = _base_rate(fill.currency, base_currency, result.as_of, fx_rate)
        notional_base = fill.notional * rate
        if fill.side is OrderSide.BUY:
            net += notional_base
        else:
            net -= notional_base
    return net


def compute_attribution(
    results: Sequence[DailyResult],
    *,
    base_currency: Currency,
    initial_capital: float,
    fx_rate: Callable[[Currency, Currency, date], float | None],
    tolerance: float = 1e-6,
) -> AttributionReport:
    """Compute the per-day attribution of a run (SP 2.57).

    Args:
        results: The daily results in strictly ascending date order (SP 2.51).
        base_currency: The reporting (benchmark) currency.
        initial_capital: The net value of the baseline (the day before the
            first result); the baseline must be cash-only.
        fx_rate: Returns base units per one unit of the source currency for a
            day, or ``None`` when unavailable (SP 2.12).
        tolerance: The per-day reconciliation tolerance.

    Returns:
        An :class:`AttributionReport` whose buckets sum to the net-value change.

    Raises:
        AttributionError: If the result series is empty, not in strictly
            ascending date order, contains a non-positive net value, has a
            non-positive initial capital, or needs a missing FX rate.
    """
    if not results:
        raise AttributionError("At least one daily result is required for attribution.")
    if initial_capital <= 0:
        raise AttributionError("initial_capital must be positive.")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative.")
    if any(result.valuation.net_value.total_value <= 0 for result in results):
        raise AttributionError("Net values must all be positive to attribute returns.")
    if any(before.as_of >= after.as_of for before, after in zip(results, results[1:])):
        raise AttributionError("Results must be in strictly ascending date order.")

    days: list[DailyAttribution] = []
    previous_value = initial_capital
    previous_securities = 0.0
    previous_fees = 0.0
    previous_fx_pnl = 0.0
    for result in results:
        valuation = result.valuation
        net = valuation.net_value
        value = net.total_value
        securities = net.securities_value
        fees = net.fees_paid
        fx_pnl = valuation.fx_pnl
        day = result.as_of

        change = value - previous_value
        dividends = _day_dividends(result, base_currency, fx_rate)
        corporate_actions = _day_corporate_cash(result)
        trading_costs = -(fees - previous_fees)
        fx_impact = fx_pnl - previous_fx_pnl
        price_return = (securities - previous_securities) - _net_buy(result, base_currency, fx_rate)
        gap = change - (price_return + dividends + corporate_actions + trading_costs + fx_impact)
        days.append(
            DailyAttribution(
                as_of=day,
                previous_value=previous_value,
                net_value=value,
                net_value_change=change,
                price_return=price_return,
                dividends=dividends,
                corporate_actions=corporate_actions,
                trading_costs=trading_costs,
                fx_impact=fx_impact,
                gap=gap,
            )
        )
        previous_value = value
        previous_securities = securities
        previous_fees = fees
        previous_fx_pnl = fx_pnl

    return AttributionReport(
        base_currency=base_currency,
        initial_capital=initial_capital,
        tolerance=tolerance,
        days=tuple(days),
    )
