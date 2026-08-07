"""Drawdown event analysis (MVP 2 / SP 2.56).

Marks threshold-triggered drawdown intervals (default 5% / 8% / 10%) over a
daily net-value series (SP 2.45 :class:`~harbor.core.valuation.DailyValuation`)
and records, for each interval, the positions held at the trough (the valuation
snapshot, 当时持仓) and the market / currency / individual exposure held then
(SP 2.55 :class:`~harbor.core.exposure.ExposurePoint`, 市场与币种暴露).

A drawdown interval starts on the first day the value falls at or below
``peak * (1 - threshold)`` and ends on the first day it recovers to the
pre-drawdown peak (or stays open if the series ends first). The depth is the
worst peak-to-trough decline within the interval, so it can exceed the
threshold.

The events are research-only warnings (仅用于研究告警, 不构成投资建议): they
never trigger orders and never alter the run. An empty or single-day series, a
series that is not in strictly ascending date order, or a non-positive net
value raises :class:`DrawdownError` rather than fabricating an interval
(matching the never-assume/never-fabricate rule).

Pure core logic: depends only on the valuation and exposure types; never
touches storage or CLI code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from harbor.core.exposure import ExposurePoint, ExposureSeries
from harbor.core.valuation import DailyValuation


class DrawdownError(ValueError):
    """Raised when drawdown events cannot be computed (SP 2.56)."""


@dataclass(frozen=True)
class DrawdownConfig:
    """The drawdown thresholds to report (SP 2.56)."""

    thresholds: tuple[float, ...] = (0.05, 0.08, 0.10)

    def __post_init__(self) -> None:
        if not self.thresholds:
            raise ValueError("At least one drawdown threshold is required.")
        if any(not (0.0 < threshold < 1.0) for threshold in self.thresholds):
            raise ValueError("Drawdown thresholds must be strictly between 0 and 1.")
        if any(a >= b for a, b in zip(self.thresholds, self.thresholds[1:])):
            raise ValueError("Drawdown thresholds must be strictly ascending.")


@dataclass(frozen=True)
class DrawdownEvent:
    """One threshold-triggered drawdown interval (SP 2.56)."""

    threshold: float
    start_date: date
    peak_date: date
    peak_value: float
    trough_date: date
    trough_value: float
    depth: float
    recovered_date: date | None
    trough_valuation: DailyValuation
    trough_exposure: ExposurePoint | None

    def readable(self) -> str:
        """Render the drawdown interval as a research warning."""
        status = (
            f"recovered {self.recovered_date.isoformat()}"
            if self.recovered_date is not None
            else "still open"
        )
        return (
            f"drawdown >= {self.threshold:.0%} on {self.start_date.isoformat()}: "
            f"peak {self.peak_value:.2f} on {self.peak_date.isoformat()}, "
            f"trough {self.trough_value:.2f} on {self.trough_date.isoformat()} "
            f"(depth {self.depth:.2%}, {status})"
        )


@dataclass(frozen=True)
class DrawdownSeries:
    """All threshold-triggered drawdown intervals for a run (SP 2.56)."""

    config: DrawdownConfig
    events: tuple[DrawdownEvent, ...]

    def for_threshold(self, threshold: float) -> tuple[DrawdownEvent, ...]:
        """Return the intervals that crossed the given threshold."""
        return tuple(event for event in self.events if event.threshold == threshold)

    def readable(self) -> str:
        """Render the drawdown events as research warnings."""
        if not self.events:
            return "No drawdown events crossed the configured thresholds."
        lines = ["Drawdown events (research-only warning, 不构成投资建议):"]
        lines.extend(event.readable() for event in self.events)
        return "\n".join(lines)


@dataclass(frozen=True)
class _RawInterval:
    """Internal interval record before exposure enrichment (SP 2.56)."""

    start_date: date
    peak_date: date
    peak_value: float
    trough_date: date
    trough_value: float
    recovered_date: date | None


def _find_intervals(
    pairs: Sequence[tuple[date, float]],
    threshold: float,
) -> tuple[_RawInterval, ...]:
    """Return the intervals that crossed ``threshold``, in date order."""
    intervals: list[_RawInterval] = []
    peak_date, peak_value = pairs[0]
    in_drawdown = False
    start_date: date = peak_date
    trough_date: date = peak_date
    trough_value: float = peak_value
    for day, value in pairs[1:]:
        if in_drawdown:
            if value < trough_value:
                trough_date, trough_value = day, value
            if value >= peak_value:
                intervals.append(
                    _RawInterval(
                        start_date,
                        peak_date,
                        peak_value,
                        trough_date,
                        trough_value,
                        day,
                    )
                )
                in_drawdown = False
                if value > peak_value:
                    peak_date, peak_value = day, value
        elif value > peak_value:
            peak_date, peak_value = day, value
        elif value <= peak_value * (1.0 - threshold):
            in_drawdown = True
            start_date = day
            trough_date, trough_value = day, value
    if in_drawdown:
        intervals.append(
            _RawInterval(start_date, peak_date, peak_value, trough_date, trough_value, None)
        )
    return tuple(intervals)


def _valuations(valuations: Sequence[DailyValuation]) -> tuple[tuple[date, float], ...]:
    """Validate and return the ``(date, total value)`` series (SP 2.56)."""
    if len(valuations) < 2:
        raise DrawdownError("At least two valuations are required to detect drawdowns.")
    if any(before.as_of >= after.as_of for before, after in zip(valuations, valuations[1:])):
        raise DrawdownError("Valuations must be in strictly ascending date order.")
    pairs = tuple((valuation.as_of, valuation.net_value.total_value) for valuation in valuations)
    if any(value <= 0 for _, value in pairs):
        raise DrawdownError("Net values must all be positive to detect drawdowns.")
    return pairs


def compute_drawdown_events(
    valuations: Sequence[DailyValuation],
    *,
    config: DrawdownConfig | None = None,
    exposure: ExposureSeries | None = None,
) -> DrawdownSeries:
    """Compute the threshold-triggered drawdown intervals (SP 2.56).

    Args:
        valuations: The daily valuations in strictly ascending date order.
        config: The thresholds to report; defaults to 5% / 8% / 10%.
        exposure: Optional SP 2.55 exposure series aligned to the same days;
            when supplied, each event records the market / currency /
            individual exposure held at its trough.

    Returns:
        A :class:`DrawdownSeries` with one event per crossed threshold interval.

    Raises:
        DrawdownError: If the series is empty or single-day, not in ascending
            date order, or contains a non-positive net value.
    """
    effective = config if config is not None else DrawdownConfig()
    pairs = _valuations(valuations)
    valuation_by_date: dict[date, DailyValuation] = {
        valuation.as_of: valuation for valuation in valuations
    }
    exposure_by_date: dict[date, ExposurePoint] = (
        {point.as_of: point for point in exposure.points} if exposure is not None else {}
    )

    events: list[DrawdownEvent] = []
    for threshold in effective.thresholds:
        for interval in _find_intervals(pairs, threshold):
            events.append(
                DrawdownEvent(
                    threshold=threshold,
                    start_date=interval.start_date,
                    peak_date=interval.peak_date,
                    peak_value=interval.peak_value,
                    trough_date=interval.trough_date,
                    trough_value=interval.trough_value,
                    depth=1.0 - interval.trough_value / interval.peak_value,
                    recovered_date=interval.recovered_date,
                    trough_valuation=valuation_by_date[interval.trough_date],
                    trough_exposure=exposure_by_date.get(interval.trough_date),
                )
            )
    return DrawdownSeries(config=effective, events=tuple(events))
