"""Target positions and order drafts (MVP 2 / SP 2.36).

Given the constraint-adjusted target portfolio (SP 2.35), the current
positions, prices in each market's quote currency, the total portfolio value
and the available cash, this module derives the buy/sell order drafts needed to
move the portfolio to its targets.

For every selected symbol:

    target_value_base = target_weight * portfolio_value
    target_value_quote = target_value_base / fx_to_base
    target_quantity = target_value_quote / price
    delta = target_quantity - current_quantity

A positive delta yields a BUY draft, a negative delta a SELL draft, and a zero
delta (within an epsilon) is skipped. FX to the base currency is required when
a market's quote currency differs from the base (SP 2.12): a missing or
non-positive rate is refused rather than assuming 1:1, so a cross-currency
order is never silently misvalued.

The result also reports the total buy/sell notional in the base currency and
the cash shortfall when the buys exceed the available cash plus the sell
proceeds, so an infeasible target is surfaced (可用现金).

Pure core logic: depends only on the backtest domain types, the market
registry, the FX module and the concentration result; never touches storage or
CLI code.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Currency, Market, OrderSide, to_market_target
from harbor.core.concentration import ConstrainedPortfolio
from harbor.core.fx import FxConversionError
from harbor.core.market_registry import get_market_config

_EPSILON = 1e-9


def _quote_currency(market: Market) -> Currency:
    """Return the currency securities in ``market`` are quoted in."""
    return Currency(get_market_config(to_market_target(market)).currency)


@dataclass(frozen=True)
class OrderDraft:
    """A proposed buy or sell of one symbol to reach its target."""

    market: Market
    symbol: str
    side: OrderSide
    quantity: float
    currency: Currency
    target_quantity: float
    current_quantity: float

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Order draft quantity must be positive.")


@dataclass(frozen=True)
class OrderDraftResult:
    """The generated order drafts and their cash feasibility."""

    as_of: date
    base_currency: Currency
    portfolio_value: float
    drafts: tuple[OrderDraft, ...]
    skipped: tuple[tuple[Market, str], ...]
    buy_value_base: float
    sell_value_base: float
    cash_shortfall: float

    def readable(self) -> str:
        """Render the order drafts as a human-readable summary."""
        lines = [
            f"Order drafts for {self.as_of.isoformat()} "
            f"(base {self.base_currency.value}, value {self.portfolio_value:.2f}):"
        ]
        for draft in self.drafts:
            lines.append(
                f"  {draft.side.value} {draft.market.value}/{draft.symbol}: "
                f"{draft.quantity:.4f} {draft.currency.value} "
                f"(target {draft.target_quantity:.4f}, current {draft.current_quantity:.4f})"
            )
        lines.append(f"buy value {self.buy_value_base:.2f}; sell value {self.sell_value_base:.2f}")
        lines.append(f"cash shortfall: {self.cash_shortfall:.2f}")
        if self.skipped:
            lines.append("skipped: " + ", ".join(f"{m.value}/{s}" for m, s in self.skipped))
        return "\n".join(lines)


def generate_order_drafts(
    portfolio: ConstrainedPortfolio,
    positions: Mapping[tuple[Market, str], float],
    prices: Mapping[tuple[Market, str], float],
    *,
    portfolio_value: float,
    available_cash: float,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> OrderDraftResult:
    """Generate buy/sell order drafts to reach the target weights (SP 2.36).

    Args:
        portfolio: The constraint-adjusted target portfolio (SP 2.35).
        positions: Current quantity per ``(market, symbol)``.
        prices: Current price per ``(market, symbol)`` in its quote currency.
        portfolio_value: Total portfolio value in the base currency.
        available_cash: Available cash in the base currency.
        fx_rate: Returns the latest FX rate (from → to) on or before the
            portfolio's as-of date, or ``None`` when unavailable (SP 2.12).

    Raises:
        ValueError: If ``portfolio_value`` is not positive, or a selected
            symbol has no positive price.
        FxConversionError: If a market's quote currency differs from the base
            currency and its FX rate is missing or non-positive.
    """
    if portfolio_value <= 0:
        raise ValueError("portfolio_value must be positive.")
    if available_cash < 0:
        raise ValueError("available_cash must be non-negative.")

    drafts: list[OrderDraft] = []
    skipped: list[tuple[Market, str]] = []
    buy_value_base = 0.0
    sell_value_base = 0.0

    for weight in portfolio.weights:
        market = weight.market
        symbol = weight.symbol
        price = prices.get((market, symbol))
        if price is None or price <= 0:
            raise ValueError(f"Missing or non-positive price for {market.value}/{symbol}.")
        quote_currency = _quote_currency(market)
        rate: float | None
        if quote_currency is portfolio.base_currency:
            rate = 1.0
        else:
            rate = fx_rate(quote_currency, portfolio.base_currency, portfolio.as_of)
            if rate is None or rate <= 0:
                raise FxConversionError(
                    f"Missing valid FX {quote_currency.value}->"
                    f"{portfolio.base_currency.value} as of "
                    f"{portfolio.as_of.isoformat()} for {market.value}/{symbol}; "
                    "refusing to assume 1:1 (SP 2.12)."
                )
        target_value_base = weight.weight * portfolio_value
        target_value_quote = target_value_base / rate
        target_quantity = target_value_quote / price
        current_quantity = positions.get((market, symbol), 0.0)
        delta = target_quantity - current_quantity
        if abs(delta) < _EPSILON:
            skipped.append((market, symbol))
            continue
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        quantity = abs(delta)
        base_value = quantity * price * rate
        if side is OrderSide.BUY:
            buy_value_base += base_value
        else:
            sell_value_base += base_value
        drafts.append(
            OrderDraft(
                market=market,
                symbol=symbol,
                side=side,
                quantity=quantity,
                currency=quote_currency,
                target_quantity=target_quantity,
                current_quantity=current_quantity,
            )
        )

    drafts.sort(key=lambda draft: (draft.market.value, draft.symbol))
    cash_shortfall = max(0.0, buy_value_base - (available_cash + sell_value_base))
    return OrderDraftResult(
        as_of=portfolio.as_of,
        base_currency=portfolio.base_currency,
        portfolio_value=portfolio_value,
        drafts=tuple(drafts),
        skipped=tuple(sorted(skipped, key=lambda key: (key[0].value, key[1]))),
        buy_value_base=buy_value_base,
        sell_value_base=sell_value_base,
        cash_shortfall=cash_shortfall,
    )
