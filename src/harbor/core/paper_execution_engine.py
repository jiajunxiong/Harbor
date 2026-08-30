"""Paper execution engine (MVP 4 / SP 4.50-4.52).

Matches a paper order into a fill on a trading day: the fill price follows the
configured fill rule (SP 2.39 open / close / next open), slippage moves the
price in the trade direction (SP 4.22), the traded-value participation rate
caps the quantity (成交量约束, SP 4.51) and a suspended or untradeable symbol
refuses execution entirely (停牌处理, SP 4.52). Any unfilled remainder keeps a
reason and is cancelled or deferred per the configured policy (SP 4.21), never
silently dropped.

The engine is deterministic: the same order, quotes and parameters always
produce the same fills and refusals (replayable, SP 4.59).

Pure core logic: depends on the SP 2.39 / 2.40 / 2.41 helpers, the paper fill
simulation (SP 4.22), the unfilled handling (SP 4.21) and the paper domain;
never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_config import FillRule, UnfilledPolicy
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.paper_domain import PaperFill, PaperOrder
from harbor.core.paper_fill_simulation import simulate_paper_fill
from harbor.core.paper_unfilled import PaperUnfilledOutcome, decide_unfilled
from harbor.core.suspension import is_tradeable


class PaperExecutionError(ValueError):
    """Raised when a paper order cannot be executed (SP 4.50)."""


@dataclass(frozen=True)
class PaperExecutionResult:
    """The outcome of executing one paper order (SP 4.50)."""

    order_id: str
    day: date
    fill: PaperFill | None
    unfilled: PaperUnfilledOutcome | None
    refusal_reason: str | None = None

    @property
    def executed(self) -> bool:
        """Whether the order produced a fill."""
        return self.fill is not None

    @property
    def suspended(self) -> bool:
        """Whether the order was refused due to suspension (SP 4.52)."""
        return self.refusal_reason is not None

    def readable(self) -> str:
        """Render the execution outcome as a compact summary."""
        if self.refusal_reason is not None:
            return f"order {self.order_id} refused on {self.day.isoformat()}: {self.refusal_reason}"
        fill = (
            "none" if self.fill is None else f"filled {self.fill.quantity:g} @ {self.fill.price:g}"
        )
        unfilled = "" if self.unfilled is None else f"; {self.unfilled.readable()}"
        return f"order {self.order_id} on {self.day.isoformat()}: {fill}{unfilled}"


def execute_paper_order(
    *,
    order: PaperOrder,
    day: date,
    quote: DailyQuote | None,
    next_quote: DailyQuote | None = None,
    rule: FillRule = FillRule.CLOSE,
    slippage_bps: float = 0.0,
    fee: float = 0.0,
    participation_rate: float | None = None,
    volume: int | None = None,
    unfilled_policy: UnfilledPolicy = UnfilledPolicy.CANCEL,
    fill_id: str | None = None,
) -> PaperExecutionResult:
    """Execute one paper order on ``day`` (SP 4.50).

    Args:
        order: The order to execute.
        day: The execution day.
        quote: The quote for ``day``; ``None`` or zero-volume means the symbol
            is suspended / untradeable (SP 4.52) and the order is refused.
        next_quote: The next trading day's quote (required by ``NEXT_OPEN``).
        rule: The fill rule (SP 2.39).
        slippage_bps: Directional slippage (SP 4.22).
        fee: The all-in trading fee recorded on the fill.
        participation_rate: When given (with ``volume``), caps the fill
            quantity (SP 4.51).
        volume: The day's traded volume for the symbol.
        unfilled_policy: How an unfilled remainder is handled (SP 4.21).
        fill_id: The fill id to record.

    Returns:
        The execution outcome: a fill (full or partial), an unfilled outcome
        for the remainder, or a suspension refusal.

    Raises:
        PaperExecutionError: If a participation rate is given without a volume
            (or vice versa).
    """
    if (participation_rate is None) != (volume is None):
        raise PaperExecutionError("participation_rate and volume must be supplied together.")

    if not is_tradeable(quote):
        return PaperExecutionResult(
            order_id=order.order_id,
            day=day,
            fill=None,
            unfilled=None,
            refusal_reason=(
                f"symbol {order.market.value}/{order.symbol} is suspended or "
                f"untradeable on {day.isoformat()}; no new fills."
            ),
        )
    assert quote is not None  # a tradeable symbol has a quote

    simulation = simulate_paper_fill(
        order=order,
        rule=rule,
        quote=quote,
        next_quote=next_quote,
        slippage_bps=slippage_bps,
        fee=fee,
        participation_rate=participation_rate,
        volume=volume,
        fill_id=fill_id,
    )

    unfilled: PaperUnfilledOutcome | None
    if simulation.fill is None:
        unfilled = decide_unfilled(
            order_id=order.order_id,
            requested_quantity=order.quantity,
            filled_quantity=0.0,
            policy=unfilled_policy,
            reason=simulation.basis,
        )
        return PaperExecutionResult(
            order_id=order.order_id,
            day=day,
            fill=None,
            unfilled=unfilled,
        )

    fill: PaperFill = simulation.fill
    if fill.quantity < order.quantity:
        unfilled = decide_unfilled(
            order_id=order.order_id,
            requested_quantity=order.quantity,
            filled_quantity=fill.quantity,
            policy=unfilled_policy,
            reason=simulation.basis,
        )
    else:
        unfilled = None
    return PaperExecutionResult(
        order_id=order.order_id,
        day=day,
        fill=fill,
        unfilled=unfilled,
    )
