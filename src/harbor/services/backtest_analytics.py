"""Derived backtest analytics from persisted rows (MVP 5 / SP 5.16-5.17).

The dashboard must show the *same* numbers the CLI shows. Instead of
reimplementing return, risk or drawdown maths for the API, this module maps
persisted rows back onto the MVP 2 core value types and then calls the very same
core functions the CLI uses (SP 2.53 performance metrics, SP 2.56 drawdown
events). There is one implementation of each metric, so a dashboard number
cannot drift from a report number.

**What can be reconstructed, and what cannot.** The persisted
``backtest_net_values`` rows carry cash, securities value and cumulative fees —
nothing else. Position-level values, the multi-currency ledger breakdown and
realised FX P&L are *not* persisted, so a reconstructed
:class:`~harbor.core.valuation.DailyValuation` carries empty ``position_values``
and a single base-currency cash balance, and its ``fx_pnl`` is the one field
filled in as ``0.0`` because the column does not exist. That field is never read
and never exposed; it is required only to satisfy the dataclass. Drawdown
intervals, depth and recovery dates depend on the net-value series alone and are
therefore exact.

Reconstructed totals use ``NetValue.total_value`` (cash + securities). The
persisted ``total_value`` column disagrees on 1878 of 8112 rows by at most
1e-6 — the last digit of ``Numeric(20, 6)`` — so the two agree far below any
display precision; the derived value is used because it is what the core metric
functions consume.

A run whose series is ambiguous (several currencies, or two rows for one day) is
**refused** rather than charted: a single curve would be meaningless, and
substituting an approximation is exactly what this project does not do.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from harbor.core.backtest_domain import CashBalance, Currency, NetValue
from harbor.core.drawdown_events import (
    DrawdownConfig,
    DrawdownEvent,
    compute_drawdown_events,
)
from harbor.core.performance_metrics import MetricsConfig, PerformanceMetrics
from harbor.core.performance_metrics import compute_performance_metrics as _compute_metrics
from harbor.core.valuation import DailyValuation


class BacktestAnalyticsError(ValueError):
    """Raised when persisted rows cannot support the requested analytics."""


def _as_date(value: object) -> date:
    """Read a database date (or an ISO string) as a ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise BacktestAnalyticsError(f"Cannot read {value!r} as a date.") from error
    raise BacktestAnalyticsError(f"Cannot read {value!r} as a date.")


def net_values_from_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[NetValue, ...]:
    """Rebuild the core net-value series from persisted rows, ascending by date.

    Raises:
        BacktestAnalyticsError: If there are no rows, or the series is ambiguous
            (more than one currency, or more than one row per day).
    """
    if not rows:
        raise BacktestAnalyticsError("This run has no persisted net values.")

    series = [
        NetValue(
            as_of_date=_as_date(row["as_of_date"]),
            currency=Currency(str(row["currency"])),
            cash=float(row["cash"]),
            securities_value=float(row["securities_value"]),
            fees_paid=float(row["fees_paid"]),
        )
        for row in rows
    ]
    series.sort(key=lambda snapshot: snapshot.as_of_date)

    currencies = sorted({snapshot.currency.value for snapshot in series})
    if len(currencies) > 1:
        raise BacktestAnalyticsError(
            "This run's net values span several currencies "
            f"({', '.join(currencies)}); a single curve would be meaningless, "
            "so no series is served."
        )
    dates = [snapshot.as_of_date for snapshot in series]
    if len(set(dates)) != len(dates):
        raise BacktestAnalyticsError(
            "This run has more than one net value for the same day; the series "
            "is ambiguous and is not served."
        )
    return tuple(series)


def metrics_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    config: MetricsConfig | None = None,
) -> PerformanceMetrics:
    """Compute the CLI's performance metrics over persisted net values (SP 5.16).

    Raises:
        BacktestAnalyticsError: If the series cannot be reconstructed.
        MetricsError: If the series is too short or degenerate to have metrics.
    """
    return metrics_from_net_values(net_values_from_rows(rows), config=config)


def metrics_from_net_values(
    snapshots: Sequence[NetValue],
    *,
    config: MetricsConfig | None = None,
) -> PerformanceMetrics:
    """Compute the CLI's performance metrics over an already-rebuilt series (SP 5.16).

    Raises:
        MetricsError: If the series is too short or degenerate to have metrics.
    """
    return _compute_metrics(snapshots, config=config)


@dataclass(frozen=True)
class DrawdownInterval:
    """One threshold-triggered drawdown interval, as far as persisted data allows.

    The core :class:`~harbor.core.drawdown_events.DrawdownEvent` also carries the
    trough's full :class:`~harbor.core.valuation.DailyValuation` and an optional
    exposure point. Those need position-level and FX data that is not persisted,
    so this view carries only the fields the stored series can support — the
    interval, its depth and its recovery — and lets the caller report the rest as
    unavailable instead of guessing.
    """

    threshold: float
    start_date: date
    peak_date: date
    peak_value: float
    trough_date: date
    trough_value: float
    depth: float
    recovered_date: date | None

    @classmethod
    def from_event(cls, event: DrawdownEvent) -> "DrawdownInterval":
        """Narrow a core drawdown event to the persisted-data subset."""
        return cls(
            threshold=event.threshold,
            start_date=event.start_date,
            peak_date=event.peak_date,
            peak_value=event.peak_value,
            trough_date=event.trough_date,
            trough_value=event.trough_value,
            depth=event.depth,
            recovered_date=event.recovered_date,
        )


def drawdown_events_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    config: DrawdownConfig | None = None,
) -> tuple[DrawdownInterval, ...]:
    """Compute the 5% / 8% / 10% drawdown intervals for a run (SP 5.17).

    Thresholds, depth and recovery dates come from the same core function the CLI
    report uses, so the intervals on screen are the intervals in the report.

    Raises:
        BacktestAnalyticsError: If the series cannot be reconstructed.
        DrawdownError: If the series is empty, single-day, unordered or
            non-positive.
    """
    return drawdown_events_from_net_values(net_values_from_rows(rows), config=config)


def drawdown_events_from_net_values(
    snapshots: Sequence[NetValue],
    *,
    config: DrawdownConfig | None = None,
) -> tuple[DrawdownInterval, ...]:
    """Compute drawdown intervals over an already-rebuilt series (SP 5.17).

    Raises:
        DrawdownError: If the series is empty, single-day, unordered or
            non-positive.
    """
    valuations = [_daily_valuation(snapshot) for snapshot in snapshots]
    series = compute_drawdown_events(valuations, config=config)
    return tuple(DrawdownInterval.from_event(event) for event in series.events)


def _daily_valuation(snapshot: NetValue) -> DailyValuation:
    """Rebuild the minimum valuation the drawdown computation reads.

    ``position_values`` is empty and ``fx_pnl`` is ``0.0`` because neither is
    persisted; the drawdown interval maths reads only the net-value series, so
    both are left out of the answer rather than invented. Nothing built here
    escapes this module — callers receive :class:`DrawdownInterval`.
    """
    return DailyValuation(
        as_of=snapshot.as_of_date,
        base_currency=snapshot.currency,
        cash=(CashBalance(currency=snapshot.currency, amount=snapshot.cash),),
        position_values=(),
        realized_fees=(CashBalance(currency=snapshot.currency, amount=snapshot.fees_paid),),
        fx_pnl=0.0,
        net_value=snapshot,
    )
