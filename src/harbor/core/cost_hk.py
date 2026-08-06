"""Hong Kong trading cost model (MVP 2 / SP 2.37).

Models the Hong Kong transaction costs separately from the US model (SP 2.38):
commission, stamp duty, transaction levy, trading fee, a platform minimum
charge and the board-lot (手数) rule. All rates come from the configuration
(SP 2.4 :class:`~harbor.core.backtest_config.CostConfig`), so the model is
replayable.

The commission is ``max(notional * commission_rate, min_commission)``; the
stamp duty, transaction levy and trading fee are proportional to the notional.
Every component (and the total) is rounded to HKD cents (2 decimals). The
board-lot rule rounds an order quantity DOWN to a whole number of lots
(``lot_size``, default 100), so a buy never exceeds its intended notional and
a sell never exceeds its intended quantity.

The US-only ``regulatory_fee_rate`` is deliberately not applied here; each
market keeps its own cost rules (MVP 2 acceptance criteria).

Pure core logic: depends only on the domain types and the cost config; never
touches storage or CLI code.
"""

from dataclasses import dataclass

from harbor.core.backtest_config import CostConfig
from harbor.core.backtest_domain import Market, OrderSide


@dataclass(frozen=True)
class HkOrderCost:
    """The Hong Kong cost breakdown for one order (SP 2.37)."""

    market: Market
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    notional: float
    commission: float
    stamp_duty: float
    transaction_levy: float
    trading_fee: float
    total_fee: float

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Quantity must be positive.")
        if self.price <= 0:
            raise ValueError("Price must be positive.")

    def readable(self) -> str:
        """Render the cost breakdown as a human-readable summary."""
        lines = [
            f"HK cost for {self.side.value} {self.symbol}: "
            f"{self.quantity:.2f} @ {self.price:.4f} HKD "
            f"(notional {self.notional:.2f})"
        ]
        lines.append(f"  commission: {self.commission:.2f}")
        lines.append(f"  stamp duty: {self.stamp_duty:.2f}")
        lines.append(f"  transaction levy: {self.transaction_levy:.2f}")
        lines.append(f"  trading fee: {self.trading_fee:.2f}")
        lines.append(f"  total fee: {self.total_fee:.2f}")
        return "\n".join(lines)


def _cents(amount: float) -> float:
    """Round an HKD amount to cents (2 decimals)."""
    return round(amount, 2)


def hk_order_cost(
    *,
    symbol: str,
    side: OrderSide,
    quantity: float,
    price: float,
    config: CostConfig | None = None,
) -> HkOrderCost:
    """Compute the Hong Kong transaction cost for one order (SP 2.37).

    Args:
        symbol: The traded symbol.
        side: Buy or sell; both sides incur the same HK fees.
        quantity: The number of shares (expected to be a whole number of lots).
        price: The HKD execution price.
        config: The cost parameters from the configuration (SP 2.4).

    Raises:
        ValueError: If ``quantity`` or ``price`` is not positive.
    """
    if config is None:
        config = CostConfig()
    notional = quantity * price
    commission = _cents(max(notional * config.commission_rate, config.min_commission))
    stamp_duty = _cents(notional * config.stamp_duty_rate)
    transaction_levy = _cents(notional * config.transaction_levy_rate)
    trading_fee = _cents(notional * config.trading_fee_rate)
    total_fee = _cents(commission + stamp_duty + transaction_levy + trading_fee)
    return HkOrderCost(
        market=Market.HK,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        notional=notional,
        commission=commission,
        stamp_duty=stamp_duty,
        transaction_levy=transaction_levy,
        trading_fee=trading_fee,
        total_fee=total_fee,
    )


def round_to_lot(quantity: float, lot_size: int = 100) -> float:
    """Round a quantity DOWN to the largest whole number of lots (SP 2.37).

    The board lot (手数) is ``lot_size`` shares; an order for fewer shares is
    rounded down to zero lots. Rounding down keeps a buy within its intended
    notional and a sell within its intended quantity.

    Raises:
        ValueError: If ``lot_size`` is not positive or ``quantity`` is negative.
    """
    if lot_size <= 0:
        raise ValueError("lot_size must be positive.")
    if quantity < 0:
        raise ValueError("quantity must be non-negative.")
    return (quantity // lot_size) * lot_size
