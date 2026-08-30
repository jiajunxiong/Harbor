"""Multi-currency paper account model (MVP 4 / SP 4.4).

The paper account keeps cash in a multi-currency ledger (SP 2.42: HKD, USD
and the base currency) plus positions with their cost basis. All FX is
explicit: converting cash requires a positive rate and must involve the base
currency, and valuing foreign balances/positions requires a supplied positive
rate — the account never assumes a 1:1 exchange and never performs an implicit
cross-FX conversion (SP 2.12 / MVP 4 acceptance). Fills move cash and fees in
the fill's own currency only (SP 2.42), so HKD and USD cash stay separate.

The account is immutable: every operation returns a new :class:`PaperAccountState`.

Pure core logic: depends on the backtest domain, the SP 2.42 ledger and the
paper domain; never touches storage or CLI code.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.backtest_domain import Currency, Fill, Market, OrderSide
from harbor.core.ledger import Ledger, apply_fill, convert, deposit, empty_ledger
from harbor.core.paper_domain import PaperAccount, PaperFill, PaperPosition


class PaperAccountError(ValueError):
    """Raised when a paper account operation is invalid (SP 4.4)."""


@dataclass(frozen=True)
class PaperAccountState:
    """An immutable paper account with its cash ledger and positions (SP 4.4).

    ``ledger`` holds multi-currency cash (SP 2.42); ``positions`` holds the
    open holdings with their cost basis. Conversions are explicit only — the
    account never assumes a 1:1 exchange (MVP 4 acceptance).
    """

    account: PaperAccount
    ledger: Ledger
    positions: tuple[PaperPosition, ...] = ()

    def __post_init__(self) -> None:
        if self.ledger.base_currency is not self.account.base_currency:
            raise PaperAccountError(
                "The ledger base currency must match the account base currency."
            )
        keys = [(position.market, position.symbol) for position in self.positions]
        if len(set(keys)) != len(keys):
            raise PaperAccountError("Positions must not contain duplicate holdings.")

    def balance(self, currency: Currency) -> float:
        """Return the cash balance in ``currency`` (0.0 when absent)."""
        return self.ledger.balance(currency)

    def fees(self, currency: Currency) -> float:
        """Return the realized fees accrued in ``currency`` (0.0 when absent)."""
        return self.ledger.fees(currency)

    def position(self, market: Market, symbol: str) -> PaperPosition | None:
        """Return the holding for (market, symbol), or None when flat."""
        for position in self.positions:
            if position.market is market and position.symbol == symbol:
                return position
        return None

    def readable(self) -> str:
        """Render the account state as a compact summary."""
        return f"{self.account.readable()} | {self.ledger.readable()}"


@dataclass(frozen=True)
class AccountAssets:
    """Base-currency aggregation of an account (SP 4.4).

    Foreign cash and foreign positions are converted with an explicit,
    positive FX rate; a missing rate raises instead of assuming 1:1.
    """

    base_currency: Currency
    cash_base: float
    positions_base: float
    total_base: float

    def __post_init__(self) -> None:
        if self.cash_base < 0 or self.positions_base < 0:
            raise PaperAccountError("Account asset values must be non-negative.")

    def reconciled(self, tolerance: float = 1e-6) -> bool:
        """Whether assets close: total == cash + positions (SP 4.13)."""
        return abs(self.total_base - (self.cash_base + self.positions_base)) <= tolerance

    def readable(self) -> str:
        """Render the assets as a compact summary."""
        return (
            f"assets {self.base_currency.value}: cash {self.cash_base:.2f} + "
            f"positions {self.positions_base:.2f} = {self.total_base:.2f}"
        )


def open_paper_account(
    *,
    account_id: str,
    base_currency: Currency,
    currencies: tuple[Currency, ...],
    initial_capital: float,
    opened_at: date,
) -> PaperAccountState:
    """Open a paper account funded with ``initial_capital`` in the base currency.

    Raises:
        ValueError: If the account identity, currencies or capital are invalid
            (propagated from :class:`PaperAccount` / :func:`deposit`).
    """
    account = PaperAccount(
        account_id=account_id,
        base_currency=base_currency,
        currencies=currencies,
        initial_capital=initial_capital,
        opened_at=opened_at,
    )
    ledger = empty_ledger(as_of=opened_at, base_currency=base_currency)
    ledger = deposit(
        ledger,
        currency=base_currency,
        amount=initial_capital,
        base_rate=1.0,
    )
    return PaperAccountState(account=account, ledger=ledger, positions=())


def _sorted_positions(positions: tuple[PaperPosition, ...]) -> tuple[PaperPosition, ...]:
    """Return positions key-sorted by (market, symbol) for determinism."""
    return tuple(sorted(positions, key=lambda position: (position.market.value, position.symbol)))


def _apply_fill_positions(
    positions: tuple[PaperPosition, ...],
    fill: PaperFill,
) -> tuple[PaperPosition, ...]:
    """Apply a fill to the position set, updating the cost basis (SP 4.4)."""
    existing: PaperPosition | None = None
    for position in positions:
        if position.market is fill.market and position.symbol == fill.symbol:
            existing = position
            break

    if existing is None:
        if fill.side is OrderSide.SELL:
            raise PaperAccountError(
                f"Cannot sell {fill.market.value}/{fill.symbol}: no position held."
            )
        opened = PaperPosition(
            market=fill.market,
            symbol=fill.symbol,
            quantity=fill.quantity,
            average_cost=fill.price,
            currency=fill.currency,
            acquired_at=fill.trade_date,
        )
        return _sorted_positions((*positions, opened))

    if existing.currency is not fill.currency:
        raise PaperAccountError(
            f"Position currency {existing.currency.value} must match the fill "
            f"currency {fill.currency.value}."
        )

    kept = tuple(
        position
        for position in positions
        if not (position.market is fill.market and position.symbol == fill.symbol)
    )
    if fill.side is OrderSide.BUY:
        new_quantity = existing.quantity + fill.quantity
        new_average = (
            existing.quantity * existing.average_cost + fill.quantity * fill.price
        ) / new_quantity
        updated = PaperPosition(
            market=existing.market,
            symbol=existing.symbol,
            quantity=new_quantity,
            average_cost=new_average,
            currency=existing.currency,
            acquired_at=existing.acquired_at,
        )
        return _sorted_positions((*kept, updated))

    if fill.quantity > existing.quantity:
        raise PaperAccountError(
            f"Cannot sell more than held: {existing.quantity:g} of "
            f"{existing.market.value}/{existing.symbol}."
        )
    new_quantity = existing.quantity - fill.quantity
    if new_quantity <= 0:
        return kept
    updated = PaperPosition(
        market=existing.market,
        symbol=existing.symbol,
        quantity=new_quantity,
        average_cost=existing.average_cost,
        currency=existing.currency,
        acquired_at=existing.acquired_at,
    )
    return _sorted_positions((*kept, updated))


def apply_paper_fill(state: PaperAccountState, *, fill: PaperFill) -> PaperAccountState:
    """Apply a fill to the account (SP 4.4).

    Cash and fees move in the fill's own currency only (SP 2.42); positions
    update their weighted-average cost basis. No implicit FX conversion.

    Raises:
        InsufficientCashError: If the fill would overdraw the fill-currency
            cash balance.
        PaperAccountError: If a sell exceeds the held quantity or a currency
            mismatches.
    """
    backtest_fill = Fill(
        order_ref=fill.paper_order_id,
        symbol=fill.symbol,
        market=fill.market,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        currency=fill.currency,
        trade_date=fill.trade_date,
        fee=fill.fee,
    )
    new_ledger = apply_fill(state.ledger, fill=backtest_fill)
    new_positions = _apply_fill_positions(state.positions, fill)
    return replace(state, ledger=new_ledger, positions=new_positions)


def convert_paper_cash(
    state: PaperAccountState,
    *,
    from_currency: Currency,
    to_currency: Currency,
    amount: float,
    rate: float,
) -> PaperAccountState:
    """Explicitly convert ``amount`` of ``from_currency`` at ``rate`` (SP 4.4).

    The conversion must involve the base currency and use a positive rate;
    missing or non-positive rates, same-currency calls and implicit cross-FX
    are refused (:class:`FxConversionError`), and overdrawing raises
    :class:`InsufficientCashError` (propagated from the SP 2.42 ledger).
    """
    new_ledger = convert(
        state.ledger,
        from_currency=from_currency,
        to_currency=to_currency,
        amount=amount,
        rate=rate,
    )
    return replace(state, ledger=new_ledger)


def _require_rate(
    base_currency: Currency,
    currency: Currency,
    fx_rate: Callable[[Currency, Currency], float | None],
) -> float:
    """Return a positive rate to ``base`` or refuse (never assumes 1:1)."""
    if currency is base_currency:
        return 1.0
    rate = fx_rate(currency, base_currency)
    if rate is None or rate <= 0:
        raise PaperAccountError(
            f"missing or non-positive FX {currency.value}->{base_currency.value}; "
            "refusing to assume 1:1 (SP 4.4)."
        )
    return rate


def account_assets_base(
    state: PaperAccountState,
    *,
    fx_rate: Callable[[Currency, Currency], float | None],
) -> AccountAssets:
    """Aggregate cash and positions into base currency with explicit FX (SP 4.4).

    Positions are valued at their cost basis (a stage-1 research
    approximation; market-value valuation belongs to SP 4.56). Foreign cash
    and positions require an explicit positive FX rate; a missing rate raises
    :class:`PaperAccountError` instead of assuming 1:1.
    """
    cash_base = 0.0
    for balance in state.ledger.cash:
        rate = _require_rate(state.account.base_currency, balance.currency, fx_rate)
        cash_base += balance.amount * rate
    positions_base = 0.0
    for position in state.positions:
        rate = _require_rate(state.account.base_currency, position.currency, fx_rate)
        positions_base += position.cost_basis * rate
    return AccountAssets(
        base_currency=state.account.base_currency,
        cash_base=cash_base,
        positions_base=positions_base,
        total_base=cash_base + positions_base,
    )
