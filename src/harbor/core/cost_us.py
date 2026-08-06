"""US trading cost model (MVP 2 / SP 2.38).

Models the United States transaction costs separately from the Hong Kong model
(SP 2.37): commission, the (US-only) regulatory fee, configurable slippage and
the fractional-share rule. All rates come from the configuration (SP 2.4
:class:`~harbor.core.backtest_config.CostConfig`), so the model is replayable.

The commission is ``max(notional * commission_rate, min_commission)``; the
regulatory fee (the SEC Section 31 fee) is proportional to the notional and,
matching market practice, applies to sell orders only. Slippage moves the
execution price in the direction of the trade: buys pay up, sells receive
less. Every money component (and the total) is rounded to USD cents (2
decimals). ``total_cost`` = commission + regulatory fee + slippage cost.

Unlike Hong Kong (SP 2.37), the US market has no board-lot (手数) rule:
fractional shares may be traded, so quantities are kept as-is rather than
rounded down to whole lots (see :func:`round_to_fraction`).

The HK-specific ``stamp_duty_rate`` / ``transaction_levy_rate`` /
``trading_fee_rate`` are deliberately not applied here; each market keeps its
own cost rules (MVP 2 acceptance criteria).

Pure core logic: depends only on the domain types and the cost config; never
touches storage or CLI code.
"""

from dataclasses import dataclass

from harbor.core.backtest_config import CostConfig
from harbor.core.backtest_domain import Market, OrderSide


@dataclass(frozen=True)
class UsOrderCost:
    """The US cost breakdown for one order (SP 2.38)."""

    market: Market
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    exec_price: float
    notional: float
    commission: float
    regulatory_fee: float
    slippage_cost: float
    total_cost: float

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Quantity must be positive.")
        if self.price <= 0:
            raise ValueError("Price must be positive.")

    def readable(self) -> str:
        """Render the cost breakdown as a human-readable summary."""
        lines = [
            f"US cost for {self.side.value} {self.symbol}: "
            f"{self.quantity:.2f} @ {self.price:.4f} USD "
            f"(notional {self.notional:.2f})"
        ]
        lines.append(f"  exec price: {self.exec_price:.4f}")
        lines.append(f"  commission: {self.commission:.2f}")
        lines.append(f"  regulatory fee: {self.regulatory_fee:.2f}")
        lines.append(f"  slippage: {self.slippage_cost:.2f}")
        lines.append(f"  total cost: {self.total_cost:.2f}")
        return "\n".join(lines)


def _cents(amount: float) -> float:
    """Round a USD amount to cents (2 decimals)."""
    return round(amount, 2)


def us_order_cost(
    *,
    symbol: str,
    side: OrderSide,
    quantity: float,
    price: float,
    config: CostConfig | None = None,
) -> UsOrderCost:
    """Compute the United States transaction cost for one order (SP 2.38).

    Args:
        symbol: The traded symbol.
        side: Buy or sell. The regulatory fee applies to sell orders only;
            slippage always moves the price in the direction of the trade.
        quantity: The number of (possibly fractional) shares.
        price: The USD reference price before slippage.
        config: The cost parameters from the configuration (SP 2.4).

    Raises:
        ValueError: If ``quantity`` or ``price`` is not positive.
    """
    if config is None:
        config = CostConfig()
    slippage = config.slippage_bps / 10_000.0
    if side is OrderSide.BUY:
        exec_price = price * (1.0 + slippage)
    else:
        exec_price = price * (1.0 - slippage)
    notional = quantity * exec_price
    commission = _cents(max(notional * config.commission_rate, config.min_commission))
    if side is OrderSide.SELL:
        regulatory_fee = _cents(notional * config.regulatory_fee_rate)
    else:
        regulatory_fee = 0.0
    slippage_cost = _cents(abs(quantity * (exec_price - price)))
    total_cost = _cents(commission + regulatory_fee + slippage_cost)
    return UsOrderCost(
        market=Market.US,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        exec_price=exec_price,
        notional=notional,
        commission=commission,
        regulatory_fee=regulatory_fee,
        slippage_cost=slippage_cost,
        total_cost=total_cost,
    )


def round_to_fraction(quantity: float) -> float:
    """Keep a US order quantity as-is (fractional shares allowed, SP 2.38).

    The US market has no board-lot (手数) rule: fractional shares may be
    traded, so the quantity is returned unchanged rather than rounded down to
    whole lots (contrast with :func:`harbor.core.cost_hk.round_to_lot`).

    Raises:
        ValueError: If ``quantity`` is not positive.
    """
    if quantity <= 0:
        raise ValueError("Quantity must be positive.")
    return quantity
