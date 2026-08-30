"""Target portfolio derivation for the paper loop (MVP 4 / SP 4.15).

Derives the target portfolio from a validated signal intention (SP 4.14), the
current positions, the available cash and the FX prices, preserving the
calculation basis for every symbol (目标组合派生保留计算依据). For each target
weight:

    target_value_base   = weight * portfolio_value
    target_value_quote  = target_value_base / fx_to_base
    target_quantity     = target_value_quote / price

FX to the base currency is required when a market's quote currency differs
from the base (SP 2.12): a missing or non-positive rate is refused rather than
assuming 1:1. The result carries the per-symbol basis (weight, price, fx rate,
target values and quantity) plus the buy/sell notional in the base currency
and the cash shortfall when the buys exceed the available cash plus the sell
proceeds (可用现金).

Pure core logic: depends on the backtest domain, the market registry, the FX
module and the paper signal; never touches storage or CLI code.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Currency, Market, to_market_target
from harbor.core.fx import FxConversionError
from harbor.core.market_registry import get_market_config
from harbor.core.paper_domain import SignalIntention

_EPSILON = 1e-9


class PaperTargetPortfolioError(ValueError):
    """Raised when a target portfolio cannot be derived (SP 4.15)."""


def _quote_currency(market: Market) -> Currency:
    """Return the currency securities in ``market`` are quoted in."""
    return Currency(get_market_config(to_market_target(market)).currency)


@dataclass(frozen=True)
class TargetPosition:
    """A target holding in the derived portfolio (SP 4.15)."""

    market: Market
    symbol: str
    quantity: float
    currency: Currency

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Target position symbol must be non-empty.")
        if self.quantity < 0:
            raise ValueError("Target position quantity must be non-negative.")

    def readable(self) -> str:
        """Render the target position as a compact summary."""
        return f"{self.market.value}/{self.symbol} qty {self.quantity:g} {self.currency.value}"


@dataclass(frozen=True)
class TargetPositionBasis:
    """The recorded calculation basis for one target position (SP 4.15)."""

    market: Market
    symbol: str
    weight: float
    price: float
    fx_rate: float
    target_value_base: float
    target_value_quote: float
    target_quantity: float

    def readable(self) -> str:
        """Render the calculation basis as a compact summary."""
        return (
            f"{self.market.value}/{self.symbol} weight {self.weight:g} price "
            f"{self.price:g} fx {self.fx_rate:g} value_base {self.target_value_base:.2f} "
            f"value_quote {self.target_value_quote:.2f} qty {self.target_quantity:g}"
        )


@dataclass(frozen=True)
class TargetPortfolio:
    """The derived target portfolio with its calculation basis (SP 4.15)."""

    as_of: date
    base_currency: Currency
    portfolio_value: float
    positions: tuple[TargetPosition, ...]
    basis: tuple[TargetPositionBasis, ...]
    buy_value_base: float
    sell_value_base: float
    cash_shortfall: float

    def __post_init__(self) -> None:
        if self.portfolio_value <= 0:
            raise PaperTargetPortfolioError("portfolio_value must be positive.")
        symbols = [(position.market, position.symbol) for position in self.positions]
        if len(set(symbols)) != len(symbols):
            raise PaperTargetPortfolioError("Target positions must be unique.")

    def weight_of(self, market: Market, symbol: str) -> float | None:
        """Return the target weight for (market, symbol), None when absent."""
        for entry in self.basis:
            if entry.market is market and entry.symbol == symbol:
                return entry.weight
        return None

    def readable(self) -> str:
        """Render the target portfolio as a compact summary."""
        lines = [
            f"target portfolio as of {self.as_of.isoformat()} "
            f"(base {self.base_currency.value}, value {self.portfolio_value:.2f}):"
        ]
        for position in self.positions:
            lines.append(f"  {position.readable()}")
        lines.append(f"buy {self.buy_value_base:.2f}; sell {self.sell_value_base:.2f}")
        lines.append(f"cash shortfall: {self.cash_shortfall:.2f}")
        return "\n".join(lines)


def derive_target_portfolio(
    *,
    signal: SignalIntention,
    positions: Mapping[tuple[Market, str], float],
    prices: Mapping[tuple[Market, str], float],
    portfolio_value: float,
    available_cash: float,
    base_currency: Currency,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> TargetPortfolio:
    """Derive the target portfolio from a signal (SP 4.15).

    Args:
        signal: The validated signal intention (SP 4.14).
        positions: Current quantity per ``(market, symbol)``.
        prices: Current price per ``(market, symbol)`` in its quote currency.
        portfolio_value: Total portfolio value in the base currency.
        available_cash: Available cash in the base currency.
        base_currency: The account's base currency (SP 4.4).
        fx_rate: Returns the latest FX rate (from -> to) on or before the
            signal's rebalance date, or ``None`` when unavailable (SP 2.12).

    Raises:
        PaperTargetPortfolioError: If ``portfolio_value`` is not positive, a
            target symbol has no positive price, or ``available_cash`` is
            negative.
        FxConversionError: If a market's quote currency differs from the base
            currency and its FX rate is missing or non-positive.
    """
    if portfolio_value <= 0:
        raise PaperTargetPortfolioError("portfolio_value must be positive.")
    if available_cash < 0:
        raise PaperTargetPortfolioError("available_cash must be non-negative.")

    target_positions: list[TargetPosition] = []
    basis_entries: list[TargetPositionBasis] = []
    buy_value_base = 0.0
    sell_value_base = 0.0

    for symbol, weight in signal.target_weights:
        market = signal.market
        price = prices.get((market, symbol))
        if price is None or price <= 0:
            raise PaperTargetPortfolioError(
                f"Missing or non-positive price for {market.value}/{symbol}."
            )
        quote_currency = _quote_currency(market)
        rate: float | None
        if quote_currency is base_currency:
            rate = 1.0
        else:
            rate = fx_rate(quote_currency, base_currency, signal.rebalance_date)
            if rate is None or rate <= 0:
                raise FxConversionError(
                    f"Missing valid FX {quote_currency.value}->{base_currency.value} "
                    f"as of {signal.rebalance_date.isoformat()} for "
                    f"{market.value}/{symbol}; refusing to assume 1:1 (SP 2.12)."
                )
        target_value_base = weight * portfolio_value
        target_value_quote = target_value_base / rate
        target_quantity = target_value_quote / price
        current_quantity = positions.get((market, symbol), 0.0)
        delta = target_quantity - current_quantity
        if abs(delta) >= _EPSILON:
            base_value = abs(delta) * price * rate
            if delta > 0:
                buy_value_base += base_value
            else:
                sell_value_base += base_value
        target_positions.append(
            TargetPosition(
                market=market,
                symbol=symbol,
                quantity=target_quantity,
                currency=quote_currency,
            )
        )
        basis_entries.append(
            TargetPositionBasis(
                market=market,
                symbol=symbol,
                weight=weight,
                price=price,
                fx_rate=rate,
                target_value_base=target_value_base,
                target_value_quote=target_value_quote,
                target_quantity=target_quantity,
            )
        )

    target_positions.sort(key=lambda position: (position.market.value, position.symbol))
    basis_entries.sort(key=lambda entry: (entry.market.value, entry.symbol))
    cash_shortfall = max(0.0, buy_value_base - (available_cash + sell_value_base))
    return TargetPortfolio(
        as_of=signal.rebalance_date,
        base_currency=base_currency,
        portfolio_value=portfolio_value,
        positions=tuple(target_positions),
        basis=tuple(basis_entries),
        buy_value_base=buy_value_base,
        sell_value_base=sell_value_base,
        cash_shortfall=cash_shortfall,
    )
