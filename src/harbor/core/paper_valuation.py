"""Daily valuation for the paper loop (MVP 4 / SP 4.56).

Valuates the paper account on a day: cash and realized fees per currency, each
position's market value in its quote currency and in the base currency, and
the base-currency net value (净值). The missing-price rule is traceable: a
position with no quote is valued at the last available close with a warning
(SP 2.41 LAST_PRICE), and a position with no price at all is recorded as a
missing price (缺价规则可追溯) and excluded — never fabricated. Foreign cash and
positions require an explicit positive FX rate; a missing rate refuses rather
than assuming 1:1 (SP 2.12).

Pure core logic: depends on the SP 2.41 suspension valuation, the paper
account (SP 4.4) and the backtest domain; never touches storage or CLI code.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import CashBalance, Currency, Market, NetValue
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.paper_account import PaperAccountState
from harbor.core.suspension import position_valuation_price


class PaperValuationError(ValueError):
    """Raised when the paper account cannot be valued (SP 4.56)."""


@dataclass(frozen=True)
class PaperPositionValue:
    """The valued market value of one paper position (SP 4.56)."""

    market: Market
    symbol: str
    quantity: float
    price: float
    currency: Currency
    fx_rate: float
    market_value_quote: float
    market_value_base: float
    carried_forward: bool = False
    warning: str | None = None

    def readable(self) -> str:
        """Render the position value as a compact summary."""
        tag = " (carried forward)" if self.carried_forward else ""
        return (
            f"{self.market.value}/{self.symbol} qty {self.quantity:g} @ "
            f"{self.price:g} {self.currency.value} = "
            f"{self.market_value_base:.2f} {self.currency.value} base{tag}"
        )


@dataclass(frozen=True)
class PaperMissingPrice:
    """A position that could not be priced (缺价规则, SP 4.56)."""

    market: Market
    symbol: str
    rule: str
    detail: str

    def readable(self) -> str:
        """Render the missing price as a compact summary."""
        return f"{self.market.value}/{self.symbol}: {self.rule} — {self.detail}"


@dataclass(frozen=True)
class PaperValuation:
    """The daily valuation of a paper account (SP 4.56).

    ``cash_base``, ``securities_base`` and ``fees_base`` are the component
    values in the base currency, computed independently from the account; the
    ``net_value`` snapshot carries the same numbers so :meth:`reconciled`
    verifies that the components and the snapshot agree (SP 4.58).
    """

    as_of: date
    base_currency: Currency
    cash: tuple[CashBalance, ...]
    position_values: tuple[PaperPositionValue, ...]
    fees_paid: tuple[CashBalance, ...]
    cash_base: float
    securities_base: float
    fees_base: float
    net_value: NetValue
    missing_prices: tuple[PaperMissingPrice, ...]

    @property
    def total_base(self) -> float:
        """Total value in the base currency."""
        return self.net_value.total_value

    def reconciled(self, tolerance: float = 1e-6) -> bool:
        """Whether the snapshot agrees with the components (SP 4.58)."""
        return (
            abs(self.total_base - (self.cash_base + self.securities_base)) <= tolerance
            and abs(self.net_value.fees_paid - self.fees_base) <= tolerance
        )

    def readable(self) -> str:
        """Render the valuation as a compact summary."""
        lines = [
            f"paper valuation {self.as_of.isoformat()} (base {self.base_currency.value}): "
            f"cash {self.cash_base:.2f} + securities {self.securities_base:.2f} "
            f"= {self.total_base:.2f}"
        ]
        for position in self.position_values:
            lines.append(f"  {position.readable()}")
        for missing in self.missing_prices:
            lines.append(f"  missing price: {missing.readable()}")
        return "\n".join(lines)


def _base_rate(
    base_currency: Currency,
    currency: Currency,
    as_of: date,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> float:
    """Return base units per one unit of ``currency``, refusing 1:1 when absent."""
    if currency is base_currency:
        return 1.0
    rate = fx_rate(currency, base_currency, as_of)
    if rate is None or rate <= 0:
        raise PaperValuationError(
            f"missing or non-positive FX {currency.value}->{base_currency.value} "
            f"on {as_of.isoformat()}; refusing to assume 1:1 (SP 2.12)."
        )
    return rate


def value_paper_account(
    *,
    account: PaperAccountState,
    as_of: date,
    quotes: Mapping[tuple[Market, str], DailyQuote | None],
    fx_rate: Callable[[Currency, Currency, date], float | None],
    last_quotes: Mapping[tuple[Market, str], DailyQuote] | None = None,
    config: object | None = None,
) -> PaperValuation:
    """Valuate the paper account on ``as_of`` (SP 4.56).

    Args:
        account: The paper account state (SP 4.4).
        as_of: The valuation day.
        quotes: The day's quote per ``(market, symbol)``; ``None`` means the
            symbol did not trade that day.
        fx_rate: Returns base units per one unit of the source currency, or
            ``None`` when unavailable (SP 2.12).
        last_quotes: The last known quote per ``(market, symbol)`` for the
            carry-forward rule (SP 2.41).
        config: The suspension configuration (SP 2.41).

    Raises:
        PaperValuationError: If a foreign cash balance or position lacks a
            positive FX rate.
    """
    base = account.account.base_currency
    cash_base = 0.0
    for balance in account.ledger.cash:
        rate = _base_rate(base, balance.currency, as_of, fx_rate)
        cash_base += balance.amount * rate

    position_values: list[PaperPositionValue] = []
    missing_prices: list[PaperMissingPrice] = []
    securities_base = 0.0
    for position in account.positions:
        quote = quotes.get((position.market, position.symbol))
        last_quote = (
            None if last_quotes is None else last_quotes.get((position.market, position.symbol))
        )
        try:
            valuation = position_valuation_price(
                market=position.market,
                symbol=position.symbol,
                day=as_of,
                quote=quote,
                last_quote=last_quote,
                config=config,  # type: ignore[arg-type]
            )
        except ValueError as exc:
            missing_prices.append(
                PaperMissingPrice(
                    market=position.market,
                    symbol=position.symbol,
                    rule="no_price",
                    detail=str(exc),
                )
            )
            continue
        rate = _base_rate(base, position.currency, as_of, fx_rate)
        market_value_quote = position.quantity * valuation.price
        market_value_base = market_value_quote * rate
        securities_base += market_value_base
        position_values.append(
            PaperPositionValue(
                market=position.market,
                symbol=position.symbol,
                quantity=position.quantity,
                price=valuation.price,
                currency=position.currency,
                fx_rate=rate,
                market_value_quote=market_value_quote,
                market_value_base=market_value_base,
                carried_forward=valuation.carried_forward,
                warning=(None if valuation.warning is None else valuation.warning.readable()),
            )
        )

    fees_base = 0.0
    for balance in account.ledger.realized_fees:
        rate = _base_rate(base, balance.currency, as_of, fx_rate)
        fees_base += balance.amount * rate

    net_value = NetValue(
        as_of_date=as_of,
        currency=base,
        cash=cash_base,
        securities_value=securities_base,
        fees_paid=fees_base,
    )
    return PaperValuation(
        as_of=as_of,
        base_currency=base,
        cash=account.ledger.cash,
        position_values=tuple(position_values),
        fees_paid=account.ledger.realized_fees,
        cash_base=cash_base,
        securities_base=securities_base,
        fees_base=fees_base,
        net_value=net_value,
        missing_prices=tuple(missing_prices),
    )
