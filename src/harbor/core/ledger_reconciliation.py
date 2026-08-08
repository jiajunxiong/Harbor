"""Ledger reconciliation check (MVP 2 / SP 2.63).

Verifies, for every trading day, that the ledger closes (账本对账校验):

- daily assets = cash + position market values (每日资产 = 现金 + 持仓市值, SP 2.45);
- the cash balance change equals what the day's events imply: sells minus buys
  (both including fees), plus dividends, corporate-action cash and the FX
  translation P&L (SP 2.42 / 2.43 / 2.44);
- the fee ledger closes: the cumulative-fee delta matches the day's fill fees.

It consumes the SP 2.58 results artifact, so the checks run against the same
serialized numbers that are persisted, and an inconsistent row (e.g. a net
value whose cash + securities do not sum to the total) is caught. A missing FX
rate or an untraceable corporate-action currency raises
:class:`ReconciliationError` rather than assuming a value (never-assume rule).

Pure core logic: depends only on the SP 2.58 artifact and the domain currency
types; never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from harbor.core.backtest_domain import Currency


class ReconciliationError(ValueError):
    """Raised when the ledger cannot be reconciled (SP 2.63)."""


_REQUIRED = (
    "run",
    "config",
    "net_values",
    "trades",
    "dividends",
    "corporate_actions",
    "positions",
)


@dataclass(frozen=True)
class DailyLedgerReconciliation:
    """The ledger reconciliation of one trading day (SP 2.63)."""

    as_of: date
    total_value: float
    cash: float
    securities_value: float
    assets_balance: float
    assets_balanced: bool
    net_value_change: float
    cash_change: float
    expected_cash_change: float
    cash_gap: float
    cash_closes: bool
    fees_delta: float
    fees_expected: float
    fees_close: bool
    dividends: float
    corporate_actions: float
    fx_pnl_delta: float

    def readable(self) -> str:
        """Render the day's reconciliation summary."""
        status = (
            "ok" if (self.assets_balanced and self.cash_closes and self.fees_close) else "MISMATCH"
        )
        return (
            f"{self.as_of.isoformat()}: assets {self.total_value:.2f} = "
            f"cash {self.cash:.2f} + securities {self.securities_value:.2f} "
            f"(balance {self.assets_balance:.2e}); "
            f"cash change {self.cash_change:+.2f} vs expected {self.expected_cash_change:+.2f} "
            f"(gap {self.cash_gap:.2e}); "
            f"fees {self.fees_delta:.2f} vs {self.fees_expected:.2f}; "
            f"dividends {self.dividends:.2f}, corporate {self.corporate_actions:.2f}, "
            f"fx {self.fx_pnl_delta:.2f} [{status}]"
        )


@dataclass(frozen=True)
class LedgerReconciliationReport:
    """The full ledger reconciliation for a run (SP 2.63)."""

    base_currency: Currency
    initial_capital: float
    tolerance: float
    days: tuple[DailyLedgerReconciliation, ...]

    @property
    def assets_reconciled(self) -> bool:
        """Whether every day's assets equal cash + securities."""
        return all(day.assets_balanced for day in self.days)

    @property
    def fees_reconciled(self) -> bool:
        """Whether every day's fee ledger closes."""
        return all(day.fees_close for day in self.days)

    @property
    def cash_reconciled(self) -> bool:
        """Whether every day's cash change closes against the event trail."""
        return all(day.cash_closes for day in self.days)

    @property
    def reconciled(self) -> bool:
        """Whether assets, fees and cash all close."""
        return self.assets_reconciled and self.fees_reconciled and self.cash_reconciled

    def readable(self) -> str:
        """Render the reconciliation report."""
        lines = [
            f"ledger reconciliation (base {self.base_currency.value}, "
            f"initial {self.initial_capital:.2f}):"
        ]
        lines.extend(day.readable() for day in self.days)
        lines.append(
            f"  assets: {self.assets_reconciled}; fees: {self.fees_reconciled}; "
            f"cash: {self.cash_reconciled}; reconciled: {self.reconciled}"
        )
        return "\n".join(lines)


def _group_by_date(rows: list[dict[str, Any]]) -> dict[date, list[dict[str, Any]]]:
    """Group artifact rows by their ISO date."""
    grouped: dict[date, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(date.fromisoformat(str(row["date"])), []).append(row)
    return grouped


def _positions_fx_by_date(
    rows: list[dict[str, Any]],
) -> dict[date, dict[tuple[str, str], float]]:
    """Map ``(date, (market, symbol))`` to the valuation FX rate (SP 2.45)."""
    result: dict[date, dict[tuple[str, str], float]] = {}
    for row in rows:
        day = date.fromisoformat(str(row["date"]))
        result.setdefault(day, {})[(str(row["market"]), str(row["symbol"]))] = float(row["fx_rate"])
    return result


def _rate(
    currency: str,
    base: Currency,
    day: date,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> float:
    """Return base units per one unit of ``currency`` (refuse missing FX)."""
    if currency == base.value:
        return 1.0
    rate = fx_rate(Currency(currency), base, day)
    if rate is None or rate <= 0:
        raise ReconciliationError(
            f"Missing FX rate to value {currency} in {base.value} on "
            f"{day.isoformat()}; refusing to assume 1:1."
        )
    return rate


def _to_base(
    amount: float,
    currency: str,
    base: Currency,
    day: date,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> float:
    """Convert an amount to the base currency, refusing a missing FX rate."""
    return amount * _rate(currency, base, day, fx_rate)


def _position_fx(
    positions_by_day: dict[date, dict[tuple[str, str], float]],
    day: date,
    market: str,
    symbol: str,
) -> float:
    by_symbol = positions_by_day.get(day, {})
    if (market, symbol) not in by_symbol:
        raise ReconciliationError(
            f"Cannot reconcile corporate action cash on {day.isoformat()}: "
            f"position {symbol} is not held at day end; refusing to assume a currency."
        )
    return by_symbol[(market, symbol)]


def reconcile_ledger(
    artifact: dict[str, Any],
    *,
    fx_rate: Callable[[Currency, Currency, date], float | None],
    tolerance: float = 1e-6,
) -> LedgerReconciliationReport:
    """Reconcile the ledger of an SP 2.58 run artifact (SP 2.63).

    Args:
        artifact: The SP 2.58 results artifact.
        fx_rate: Returns base units per one unit of the source currency for a
            day, or ``None`` when unavailable (SP 2.12).
        tolerance: The per-day closure tolerance.

    Returns:
        A :class:`LedgerReconciliationReport` with one daily entry.

    Raises:
        ReconciliationError: If the artifact is not an SP 2.58 artifact, the
            net-value series is empty or not ascending, a foreign amount needs
            a missing FX rate, or a corporate-action cash amount has no held
            position to identify its currency.
    """
    if any(section not in artifact for section in _REQUIRED):
        raise ReconciliationError(
            "Expected an SP 2.58 results artifact with run, config and result sections."
        )
    base = Currency(artifact["run"]["base_currency"])
    initial = float(artifact["run"]["initial_capital"])
    if initial <= 0:
        raise ReconciliationError("initial_capital must be positive.")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative.")
    net_values = artifact["net_values"]
    if not net_values:
        raise ReconciliationError("At least one net value is required to reconcile the ledger.")
    days = [date.fromisoformat(str(row["date"])) for row in net_values]
    if any(before >= after for before, after in zip(days, days[1:])):
        raise ReconciliationError("Net values must be in strictly ascending date order.")

    trades_by_date = _group_by_date(artifact["trades"])
    dividends_by_date = _group_by_date(artifact["dividends"])
    corporate_by_date = _group_by_date(artifact["corporate_actions"])
    positions_by_day = _positions_fx_by_date(artifact["positions"])

    entries: list[DailyLedgerReconciliation] = []
    previous_value = initial
    previous_cash = initial
    previous_fees = 0.0
    previous_fx = 0.0
    for row in net_values:
        day = date.fromisoformat(str(row["date"]))
        value = float(row["total_value"])
        cash = float(row["cash"])
        securities = float(row["securities_value"])
        fees = float(row["fees_paid"])
        fx_pnl = float(row["fx_pnl"])

        assets_balance = value - (cash + securities)
        assets_balanced = abs(assets_balance) <= tolerance
        net_value_change = value - previous_value
        cash_change = cash - previous_cash

        buys_base = 0.0
        sells_base = 0.0
        fill_fees_base = 0.0
        for trade in trades_by_date.get(day, ()):
            rate = _rate(str(trade["currency"]), base, day, fx_rate)
            notional = float(trade["notional"]) * rate
            fee = float(trade["fee"]) * rate
            fill_fees_base += fee
            if trade["side"] == "BUY":
                buys_base += notional + fee
            else:
                sells_base += notional - fee

        dividends = sum(
            _to_base(float(dividend["gross_amount"]), str(dividend["currency"]), base, day, fx_rate)
            for dividend in dividends_by_date.get(day, ())
        )

        corporate_cash = 0.0
        for action in corporate_by_date.get(day, ()):
            cash_amount = float(action["cash_amount"])
            if cash_amount == 0.0:
                continue
            position_fx = _position_fx(
                positions_by_day, day, str(action["market"]), str(action["symbol"])
            )
            corporate_cash += cash_amount * position_fx

        fx_pnl_delta = fx_pnl - previous_fx
        expected_cash_change = sells_base - buys_base + dividends + corporate_cash + fx_pnl_delta
        cash_gap = cash_change - expected_cash_change
        cash_closes = abs(cash_gap) <= tolerance

        fees_delta = fees - previous_fees
        fees_close = abs(fees_delta - fill_fees_base) <= tolerance

        entries.append(
            DailyLedgerReconciliation(
                as_of=day,
                total_value=value,
                cash=cash,
                securities_value=securities,
                assets_balance=assets_balance,
                assets_balanced=assets_balanced,
                net_value_change=net_value_change,
                cash_change=cash_change,
                expected_cash_change=expected_cash_change,
                cash_gap=cash_gap,
                cash_closes=cash_closes,
                fees_delta=fees_delta,
                fees_expected=fill_fees_base,
                fees_close=fees_close,
                dividends=dividends,
                corporate_actions=corporate_cash,
                fx_pnl_delta=fx_pnl_delta,
            )
        )
        previous_value = value
        previous_cash = cash
        previous_fees = fees
        previous_fx = fx_pnl

    return LedgerReconciliationReport(
        base_currency=base,
        initial_capital=initial,
        tolerance=tolerance,
        days=tuple(entries),
    )
