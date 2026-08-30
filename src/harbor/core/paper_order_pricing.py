"""Paper order pricing (MVP 4 / SP 4.19).

Generates the price for a paper order as a limit, market or reference order
and records the pricing basis (定价依据) and the generation time so the price
is auditable and replayable. Market orders resolve the reference price per the
configured fill rule (SP 2.39: open / close / next open); limit orders use the
user-specified limit; reference orders use the last known reference price.
Slippage is applied directionally (buy pays up, sell receives less) and
recorded explicitly in the basis (SP 4.22 feeds from this).

Pure core logic: depends on the backtest fill-price helpers (SP 2.39), the
paper domain and the configuration; never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from harbor.core.backtest_config import FillRule
from harbor.core.backtest_domain import OrderSide
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.fill_price import apply_slippage, resolve_fill_price
from harbor.core.paper_domain import PaperOrder, PaperPriceType


class PaperOrderPricingError(ValueError):
    """Raised when an order price cannot be generated (SP 4.19)."""


def _now_utc() -> datetime:
    """Return the current UTC time (default pricing timestamp)."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PaperOrderPrice:
    """A generated price for a paper order with its basis (SP 4.19)."""

    order_id: str
    price_type: PaperPriceType
    price: float
    reference_price: float
    slippage_bps: float
    basis: str
    generated_at: datetime

    def readable(self) -> str:
        """Render the pricing record as a compact summary."""
        return (
            f"order {self.order_id} {self.price_type.value} price {self.price:g} "
            f"(reference {self.reference_price:g}, slippage {self.slippage_bps:g} bps) "
            f"at {self.generated_at.isoformat()}: {self.basis}"
        )


def price_paper_order(
    *,
    order: PaperOrder,
    price_type: PaperPriceType | None = None,
    rule: FillRule = FillRule.CLOSE,
    quote: DailyQuote | None = None,
    next_quote: DailyQuote | None = None,
    reference_price: float | None = None,
    limit_price: float | None = None,
    slippage_bps: float = 0.0,
    generated_at: datetime | None = None,
) -> PaperOrderPrice:
    """Generate and record the price of a paper order (SP 4.19).

    Args:
        order: The order to price.
        price_type: LIMIT / MARKET / REFERENCE; defaults to the order's own
            price type.
        rule: The fill rule used to resolve a market order's reference price
            (SP 2.39).
        quote: The quote for the decision day (market/reference orders).
        next_quote: The next trading day's quote (required by ``NEXT_OPEN``).
        reference_price: An explicit reference price (market/reference orders).
        limit_price: The user-specified limit price (limit orders).
        slippage_bps: Directional slippage applied to the reference price
            (SP 2.39); buys pay up, sells receive less.
        generated_at: The pricing time; defaults to the current UTC time.

    Raises:
        PaperOrderPricingError: If a limit order has no limit price, or a
            market/reference order has neither a quote nor a reference price.
        ValueError: If the reference price is not positive (from SP 2.39).
    """
    kind = price_type if price_type is not None else order.price_type
    timestamp = _now_utc() if generated_at is None else generated_at

    if kind is PaperPriceType.LIMIT:
        if limit_price is None or limit_price <= 0:
            raise PaperOrderPricingError("A limit order requires a positive limit price.")
        reference = limit_price
        basis = f"limit order priced at the user-specified limit {limit_price:g}"
        exec_price = apply_slippage(price=reference, side=order.side, slippage_bps=slippage_bps)
        if slippage_bps:
            basis += f" + slippage {slippage_bps:g} bps"
    else:
        if kind is PaperPriceType.MARKET:
            if quote is not None:
                reference = resolve_fill_price(rule=rule, quote=quote, next_quote=next_quote)
                basis = f"market order priced by rule {rule.value} at {reference:g}"
            elif reference_price is not None and reference_price > 0:
                reference = reference_price
                basis = f"market order priced at explicit reference {reference:g}"
            else:
                raise PaperOrderPricingError(
                    "A market order requires a quote or a positive reference price."
                )
        else:
            if reference_price is not None and reference_price > 0:
                reference = reference_price
            elif quote is not None:
                reference = quote.close
            else:
                raise PaperOrderPricingError(
                    "A reference order requires a reference price or a quote."
                )
            basis = f"reference order priced at the last known reference {reference:g}"
        exec_price = apply_slippage(price=reference, side=order.side, slippage_bps=slippage_bps)
        if slippage_bps:
            direction = "up" if order.side is OrderSide.BUY else "down"
            basis += f" + slippage {slippage_bps:g} bps ({direction})"

    return PaperOrderPrice(
        order_id=order.order_id,
        price_type=kind,
        price=exec_price,
        reference_price=reference,
        slippage_bps=slippage_bps,
        basis=basis,
        generated_at=timestamp,
    )
