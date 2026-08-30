"""Order draft generation for the paper loop (MVP 4 / SP 4.16).

Derives buy/sell order drafts from the target portfolio (SP 4.15) versus the
current positions: a positive delta yields a BUY draft, a negative delta a
SELL draft, and a zero delta (within an epsilon) is skipped. Drafts carry the
price type (SP 4.19) and keep the target/current quantities for traceability.
The result reports the buy/sell notional in the base currency and the cash
shortfall when the buys exceed the available cash plus the sell proceeds, so
an infeasible target is surfaced (现金不足处理) rather than silently executed.

Pure core logic: depends on the backtest domain, the target portfolio and the
paper domain; never touches storage or CLI code.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_domain import PaperPriceType
from harbor.core.paper_target_portfolio import TargetPortfolio

_EPSILON = 1e-9


class PaperOrderDraftError(ValueError):
    """Raised when order drafts cannot be derived (SP 4.16)."""


@dataclass(frozen=True)
class PaperOrderDraft:
    """A proposed buy or sell to reach the target (SP 4.16)."""

    market: Market
    symbol: str
    side: OrderSide
    quantity: float
    currency: Currency
    price_type: PaperPriceType
    target_quantity: float
    current_quantity: float

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Order draft symbol must be non-empty.")
        if self.quantity <= 0:
            raise ValueError("Order draft quantity must be positive.")

    def readable(self) -> str:
        """Render the draft as a compact summary."""
        return (
            f"{self.side.value} {self.market.value}/{self.symbol} "
            f"{self.quantity:g} {self.currency.value} "
            f"(target {self.target_quantity:g}, current {self.current_quantity:g})"
        )


@dataclass(frozen=True)
class PaperOrderDraftResult:
    """The generated order drafts and their cash feasibility (SP 4.16)."""

    as_of: date
    base_currency: Currency
    drafts: tuple[PaperOrderDraft, ...]
    skipped: tuple[tuple[Market, str], ...]
    buy_value_base: float
    sell_value_base: float
    cash_shortfall: float

    def readable(self) -> str:
        """Render the drafts as a compact summary."""
        lines = [f"order drafts for {self.as_of.isoformat()} (base {self.base_currency.value}):"]
        for draft in self.drafts:
            lines.append(f"  {draft.readable()}")
        lines.append(f"buy value {self.buy_value_base:.2f}; sell value {self.sell_value_base:.2f}")
        lines.append(f"cash shortfall: {self.cash_shortfall:.2f}")
        if self.skipped:
            lines.append("skipped: " + ", ".join(f"{m.value}/{s}" for m, s in self.skipped))
        return "\n".join(lines)


def derive_order_drafts(
    *,
    target: TargetPortfolio,
    positions: Mapping[tuple[Market, str], float],
    available_cash: float,
    price_type: PaperPriceType = PaperPriceType.REFERENCE,
) -> PaperOrderDraftResult:
    """Generate buy/sell order drafts to reach the target portfolio (SP 4.16).

    Args:
        target: The derived target portfolio (SP 4.15), whose basis records the
            price and FX rate used for each symbol.
        positions: Current quantity per ``(market, symbol)``.
        available_cash: Available cash in the base currency.
        price_type: The price type the drafts will be submitted with (SP 4.19).

    Raises:
        PaperOrderDraftError: If ``available_cash`` is negative or a target
            position has no recorded basis.
    """
    if available_cash < 0:
        raise PaperOrderDraftError("available_cash must be non-negative.")

    basis_by_symbol = {(entry.market, entry.symbol): entry for entry in target.basis}
    drafts: list[PaperOrderDraft] = []
    skipped: list[tuple[Market, str]] = []
    buy_value_base = 0.0
    sell_value_base = 0.0

    for position in target.positions:
        basis = basis_by_symbol.get((position.market, position.symbol))
        if basis is None:
            raise PaperOrderDraftError(
                f"Missing basis for {position.market.value}/{position.symbol}."
            )
        current_quantity = positions.get((position.market, position.symbol), 0.0)
        delta = position.quantity - current_quantity
        if abs(delta) < _EPSILON:
            skipped.append((position.market, position.symbol))
            continue
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        quantity = abs(delta)
        base_value = quantity * basis.price * basis.fx_rate
        if side is OrderSide.BUY:
            buy_value_base += base_value
        else:
            sell_value_base += base_value
        drafts.append(
            PaperOrderDraft(
                market=position.market,
                symbol=position.symbol,
                side=side,
                quantity=quantity,
                currency=position.currency,
                price_type=price_type,
                target_quantity=position.quantity,
                current_quantity=current_quantity,
            )
        )

    drafts.sort(key=lambda draft: (draft.market.value, draft.symbol))
    cash_shortfall = max(0.0, buy_value_base - (available_cash + sell_value_base))
    return PaperOrderDraftResult(
        as_of=target.as_of,
        base_currency=target.base_currency,
        drafts=tuple(drafts),
        skipped=tuple(skipped),
        buy_value_base=buy_value_base,
        sell_value_base=sell_value_base,
        cash_shortfall=cash_shortfall,
    )
