"""Paper fill simulation (MVP 4 / SP 4.22).

Simulates the execution of a paper order: resolves the reference price per the
configured fill rule (SP 2.39: open / close / next open), applies directional
slippage (buy pays up, sell receives less) and optionally caps the fill by the
traded-value participation rate (SP 2.40). The reference price, the execution
price and the slippage are recorded explicitly (显式记录滑点), and the fill
price used is the slippage-adjusted execution price.

When the participation rate leaves nothing to fill, the simulated fill is
``None`` and the caller records the unfilled outcome through SP 4.21 (never
silently dropped). The fill trade date follows the rule: the decision day for
OPEN/CLOSE and the next trading day for NEXT_OPEN (SP 2.39).

Pure core logic: depends on the SP 2.39 / 2.40 helpers, the paper domain and
the backtest interfaces; never touches storage or CLI code.
"""

from dataclasses import dataclass

from harbor.core.backtest_config import FillRule
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.fill_price import apply_slippage, resolve_fill_price
from harbor.core.paper_domain import PaperFill, PaperOrder
from harbor.core.volume_limit import limit_fill_quantity


class PaperFillSimulationError(ValueError):
    """Raised when a paper fill cannot be simulated (SP 4.22)."""


@dataclass(frozen=True)
class PaperFillSimulation:
    """The simulated execution of one paper order (SP 4.22)."""

    order_id: str
    reference_price: float
    exec_price: float
    slippage_bps: float
    requested_quantity: float
    filled_quantity: float
    fill: PaperFill | None
    volume_limited: bool
    basis: str

    def readable(self) -> str:
        """Render the simulation as a compact summary."""
        fill = (
            "none" if self.fill is None else f"filled {self.fill.quantity:g} @ {self.fill.price:g}"
        )
        return (
            f"order {self.order_id}: reference {self.reference_price:g} exec "
            f"{self.exec_price:g} (slippage {self.slippage_bps:g} bps), "
            f"requested {self.requested_quantity:g}, {fill}; {self.basis}"
        )


def simulate_paper_fill(
    *,
    order: PaperOrder,
    rule: FillRule = FillRule.CLOSE,
    quote: DailyQuote,
    next_quote: DailyQuote | None = None,
    slippage_bps: float = 0.0,
    fee: float = 0.0,
    participation_rate: float | None = None,
    volume: int | None = None,
    fill_id: str | None = None,
) -> PaperFillSimulation:
    """Simulate the execution of a paper order (SP 4.22).

    Args:
        order: The order to execute.
        rule: The fill rule used to resolve the reference price (SP 2.39).
        quote: The quote for the decision day.
        next_quote: The next trading day's quote (required by ``NEXT_OPEN``).
        slippage_bps: Directional slippage applied to the reference price.
        fee: The all-in trading fee recorded on the fill.
        participation_rate: When given (with ``volume``), the fill quantity is
            capped by the participation rate (SP 2.40).
        volume: The day's traded volume for the symbol.
        fill_id: The fill id to record; defaults to a generated one.

    Raises:
        PaperFillSimulationError: If ``participation_rate`` is given without
            ``volume`` (or vice versa).
    """
    if (participation_rate is None) != (volume is None):
        raise PaperFillSimulationError("participation_rate and volume must be supplied together.")
    reference = resolve_fill_price(rule=rule, quote=quote, next_quote=next_quote)
    exec_price = apply_slippage(price=reference, side=order.side, slippage_bps=slippage_bps)
    trade_date = (
        quote.day
        if rule is not FillRule.NEXT_OPEN
        else (next_quote.day if next_quote is not None else quote.day)
    )

    volume_limited: bool
    if participation_rate is not None and volume is not None:
        max_fill = limit_fill_quantity(
            quantity=order.quantity,
            reference_price=reference,
            volume=volume,
            participation_rate=participation_rate,
        )
        filled_quantity = max_fill
        basis = f"volume participation {participation_rate:g} capped quantity to {max_fill:g}"
        volume_limited = True
    else:
        filled_quantity = order.quantity
        basis = "full requested quantity filled"
        volume_limited = False

    fill: PaperFill | None
    if filled_quantity <= 0:
        fill = None
        rate = participation_rate if participation_rate is not None else 0.0
        basis = f"volume participation {rate:g} left nothing to fill"
    else:
        fill = PaperFill(
            fill_id=fill_id or f"fill-{order.order_id}",
            paper_order_id=order.order_id,
            paper_run_id=order.paper_run_id,
            market=order.market,
            symbol=order.symbol,
            side=order.side,
            quantity=filled_quantity,
            price=exec_price,
            currency=order.currency,
            trade_date=trade_date,
            fee=fee,
        )
    return PaperFillSimulation(
        order_id=order.order_id,
        reference_price=reference,
        exec_price=exec_price,
        slippage_bps=slippage_bps,
        requested_quantity=order.quantity,
        filled_quantity=filled_quantity,
        fill=fill,
        volume_limited=volume_limited,
        basis=basis,
    )
