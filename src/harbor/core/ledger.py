"""Multi-currency cash ledger (MVP 2 / SP 2.42).

Maintains cash balances separately per currency (HKD, USD and the base
currency), accumulated realized fees per currency, and realized FX translation
profit/loss (``fx_pnl``, expressed in the base currency). All conversions are
explicit: a conversion requires a positive rate and must involve the base
currency, so the ledger never assumes a 1:1 exchange and never performs an
implicit cross-FX conversion (SP 2.12 / MVP 2 acceptance criteria).

The ledger is immutable: every operation returns a new :class:`Ledger`.

Cash-acquisition-rate model
---------------------------
Each currency carries a weighted-average acquisition rate (base units per one
currency unit) set by deposits and base->foreign conversions. Removing cash
pro-rata (spending on a buy, converting to base) leaves the average unchanged.
Realized FX translation P&L is booked when foreign cash is converted into the
base currency: it is the difference between the base received at the conversion
rate and the base cost of that cash at its acquisition rate. Fills move cash
and accrue fees in the trade currency without changing acquisition rates.

Pure core logic: depends only on the domain types and the FX module; never
touches storage or CLI code.
"""

from dataclasses import dataclass, replace
from datetime import date

from harbor.core.backtest_domain import CashBalance, Currency, Fill, OrderSide
from harbor.core.fx import FxConversionError


class InsufficientCashError(ValueError):
    """Raised when an operation would drive a cash balance negative (SP 2.42)."""


@dataclass(frozen=True)
class AcquisitionRate:
    """Weighted-average base-currency acquisition rate for a currency (SP 2.42).

    ``rate`` is the number of base-currency units per one unit of ``currency``.
    """

    currency: Currency
    rate: float

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError("Acquisition rate must be positive.")


@dataclass(frozen=True)
class Ledger:
    """A multi-currency cash ledger with realized fees and FX P&L (SP 2.42)."""

    as_of: date
    base_currency: Currency
    cash: tuple[CashBalance, ...] = ()
    realized_fees: tuple[CashBalance, ...] = ()
    acquisition_rates: tuple[AcquisitionRate, ...] = ()
    fx_pnl: float = 0.0

    def __post_init__(self) -> None:
        if len({entry.currency for entry in self.cash}) != len(self.cash):
            raise ValueError("Cash must not contain duplicate currencies.")
        if len({entry.currency for entry in self.realized_fees}) != len(self.realized_fees):
            raise ValueError("Realized fees must not contain duplicate currencies.")
        if len({entry.currency for entry in self.acquisition_rates}) != len(self.acquisition_rates):
            raise ValueError("Acquisition rates must not contain duplicate currencies.")

    def balance(self, currency: Currency) -> float:
        """Return the cash balance in ``currency`` (0.0 when absent)."""
        for entry in self.cash:
            if entry.currency is currency:
                return entry.amount
        return 0.0

    def fees(self, currency: Currency) -> float:
        """Return the realized fees accrued in ``currency`` (0.0 when absent)."""
        for entry in self.realized_fees:
            if entry.currency is currency:
                return entry.amount
        return 0.0

    def acquisition_rate(self, currency: Currency) -> float:
        """Return the acquisition rate for ``currency`` (0.0 when absent)."""
        for entry in self.acquisition_rates:
            if entry.currency is currency:
                return entry.rate
        return 0.0

    def currencies(self) -> tuple[Currency, ...]:
        """Return the currencies that currently hold cash."""
        return tuple(entry.currency for entry in self.cash)

    def readable(self) -> str:
        """Render the ledger as a human-readable summary."""
        lines = [f"ledger as of {self.as_of.isoformat()} (base {self.base_currency.value})"]
        if not self.cash:
            lines.append("  cash: none")
        for entry in self.cash:
            lines.append(f"  cash {entry.currency.value}: {entry.amount:.2f}")
        for entry in self.realized_fees:
            lines.append(f"  realized fees {entry.currency.value}: {entry.amount:.2f}")
        lines.append(f"  fx pnl (base): {self.fx_pnl:.2f}")
        return "\n".join(lines)


def _cash_tuple(balances: dict[Currency, float]) -> tuple[CashBalance, ...]:
    """Build a sorted, zero-free tuple of cash balances."""
    return tuple(
        CashBalance(currency=currency, amount=amount)
        for currency, amount in sorted(balances.items(), key=lambda kv: kv[0].value)
        if amount != 0.0
    )


def _fees_tuple(fees: dict[Currency, float]) -> tuple[CashBalance, ...]:
    """Build a sorted, zero-free tuple of realized fees."""
    return tuple(
        CashBalance(currency=currency, amount=amount)
        for currency, amount in sorted(fees.items(), key=lambda kv: kv[0].value)
        if amount != 0.0
    )


def _acq_tuple(rates: dict[Currency, float]) -> tuple[AcquisitionRate, ...]:
    """Build a sorted tuple of positive acquisition rates."""
    return tuple(
        AcquisitionRate(currency=currency, rate=rate)
        for currency, rate in sorted(rates.items(), key=lambda kv: kv[0].value)
        if rate > 0.0
    )


def _cash_map(ledger: Ledger) -> dict[Currency, float]:
    return {entry.currency: entry.amount for entry in ledger.cash}


def _fees_map(ledger: Ledger) -> dict[Currency, float]:
    return {entry.currency: entry.amount for entry in ledger.realized_fees}


def _acq_map(ledger: Ledger) -> dict[Currency, float]:
    return {entry.currency: entry.rate for entry in ledger.acquisition_rates}


def _weighted(
    current: float,
    current_amount: float,
    add_amount: float,
    add_rate: float,
) -> float:
    """Weighted-average acquisition rate after adding ``add_amount`` at ``add_rate``."""
    total = current_amount + add_amount
    if total <= 0:
        return 0.0
    return (current * current_amount + add_rate * add_amount) / total


def empty_ledger(*, as_of: date, base_currency: Currency) -> Ledger:
    """Create an empty ledger with no cash, fees, rates or FX P&L (SP 2.42)."""
    return Ledger(
        as_of=as_of,
        base_currency=base_currency,
        cash=(),
        realized_fees=(),
        acquisition_rates=(),
        fx_pnl=0.0,
    )


def deposit(ledger: Ledger, *, currency: Currency, amount: float, base_rate: float) -> Ledger:
    """Fund the account with ``amount`` of ``currency`` (SP 2.42).

    ``base_rate`` is the base-currency value of one unit of ``currency`` at the
    moment of the deposit (1.0 for the base currency). The currency's weighted
    average acquisition rate is updated accordingly.

    Raises:
        ValueError: If ``amount`` is not positive.
        FxConversionError: If ``base_rate`` is not positive.
    """
    if amount <= 0:
        raise ValueError("Deposit amount must be positive.")
    if base_rate <= 0:
        raise FxConversionError(f"Deposit base rate must be positive, got {base_rate}.")
    cash = _cash_map(ledger)
    acq = _acq_map(ledger)
    current = cash.get(currency, 0.0)
    current_rate = acq.get(currency, 0.0)
    cash[currency] = current + amount
    acq[currency] = _weighted(current_rate, current, amount, base_rate)
    return replace(
        ledger,
        cash=_cash_tuple(cash),
        acquisition_rates=_acq_tuple(acq),
    )


def apply_fill(ledger: Ledger, *, fill: Fill) -> Ledger:
    """Apply a fill to the ledger (SP 2.42).

    Moves cash in the fill's own currency (a buy pays notional + fee, a sell
    receives notional - fee) and accrues the fee into that currency's realized
    fees. No FX conversion happens here and acquisition rates are unchanged.

    Raises:
        InsufficientCashError: If the resulting cash balance would be negative.
    """
    cash = _cash_map(ledger)
    fees = _fees_map(ledger)
    currency = fill.currency
    current = cash.get(currency, 0.0)
    notional = fill.quantity * fill.price
    if fill.side is OrderSide.BUY:
        delta = -(notional + fill.fee)
    else:
        delta = notional - fill.fee
    new_balance = current + delta
    if new_balance < 0:
        raise InsufficientCashError(
            f"Insufficient {currency.value} cash for {fill.side.value} {fill.symbol}: "
            f"balance would be {new_balance:.2f}."
        )
    cash[currency] = new_balance
    fees[currency] = fees.get(currency, 0.0) + fill.fee
    return replace(ledger, cash=_cash_tuple(cash), realized_fees=_fees_tuple(fees))


def convert(
    ledger: Ledger,
    *,
    from_currency: Currency,
    to_currency: Currency,
    amount: float,
    rate: float,
) -> Ledger:
    """Convert ``amount`` of ``from_currency`` into ``to_currency`` (SP 2.42).

    Conversion is explicit: ``rate`` (units of ``to_currency`` per one unit of
    ``from_currency``) must be positive and one of the two currencies must be
    the base currency — cross-FX is refused rather than routed through an
    implicit step. Realized FX translation P&L is booked when foreign cash is
    converted into the base currency, measured against the tracked acquisition
    rate.

    Raises:
        FxConversionError: If the currencies are equal, neither leg is the base
            currency, or ``rate`` is not positive.
        InsufficientCashError: If ``amount`` exceeds the source cash balance.
    """
    if from_currency is to_currency:
        raise FxConversionError("Convert requires two distinct currencies.")
    if rate <= 0:
        raise FxConversionError(f"FX rate must be positive, got {rate}.")
    if from_currency is not ledger.base_currency and to_currency is not ledger.base_currency:
        raise FxConversionError(
            "Conversion must involve the base currency; refusing implicit "
            "cross-FX conversion (SP 2.42)."
        )
    cash = _cash_map(ledger)
    acq = _acq_map(ledger)
    source = cash.get(from_currency, 0.0)
    if source < amount:
        raise InsufficientCashError(
            f"Insufficient {from_currency.value} cash to convert: "
            f"need {amount:.2f}, have {source:.2f}."
        )
    target = cash.get(to_currency, 0.0)
    to_amount = amount * rate
    fx_pnl = ledger.fx_pnl
    if from_currency is ledger.base_currency:
        cash[from_currency] = source - amount
        cash[to_currency] = target + to_amount
        current_rate = acq.get(to_currency, 0.0)
        acq[to_currency] = _weighted(current_rate, target, to_amount, 1.0 / rate)
    else:
        cost = amount * acq.get(from_currency, 0.0)
        fx_pnl = fx_pnl + (to_amount - cost)
        cash[from_currency] = source - amount
        cash[to_currency] = target + to_amount
    return replace(
        ledger,
        cash=_cash_tuple(cash),
        acquisition_rates=_acq_tuple(acq),
        fx_pnl=fx_pnl,
    )
