"""History window computation tools (MVP 2 / SP 2.16).

Provides rolling-window extraction, missing-value handling and minimum
observation gates for the factor computations (SP 2.17-2.21). Windows only ever
use data knowable on or before the decision date: input is expected to come from
:func:`harbor.core.factor_alignment.align_price_history`, and the window
extractor re-filters defensively so no future-dated quote can enter a window.

A statistic returns ``None`` when a window has fewer than the configured minimum
observations, so factors surface "insufficient history" as a missing value
rather than a fabricated number. Missing observations are represented by
``None`` in numeric series and are skipped, never interpolated.

This module is pure core logic: stdlib only, no storage or CLI dependencies.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import fsum, sqrt
from statistics import fmean

from harbor.core.backtest_interfaces import DailyQuote


@dataclass(frozen=True)
class WindowConfig:
    """How a historical window is defined and when a statistic is usable.

    ``lookback_days`` is the number of calendar days of history ending on the
    decision date; ``min_observations`` is the smallest number of observations
    a window must contain for a statistic to be computed. Fewer observations
    make the statistic unavailable (``None``), never zero.
    """

    lookback_days: int = 252
    min_observations: int = 60

    def __post_init__(self) -> None:
        if self.lookback_days < 0:
            raise ValueError("lookback_days must be non-negative.")
        if self.min_observations < 0:
            raise ValueError("min_observations must be non-negative.")


@dataclass(frozen=True)
class PriceWindowResult:
    """An extracted price window with its minimum-observation gate.

    ``closes`` holds the close prices within the window, sorted ascending by
    trading day. ``observation_count`` reports how many observations the window
    actually contains (missing trading days simply mean fewer observations);
    ``sufficient`` is true when the window meets ``min_observations``.
    """

    decision_date: date
    closes: tuple[float, ...]
    min_observations: int

    @property
    def observation_count(self) -> int:
        """Return the number of observations in the window."""
        return len(self.closes)

    @property
    def sufficient(self) -> bool:
        """Return whether the window meets the minimum observation gate."""
        return self.observation_count >= self.min_observations


def window_closes(
    quotes: Sequence[DailyQuote],
    decision_date: date,
    lookback_days: int,
) -> tuple[float, ...]:
    """Return close prices within a lookback window ending on ``decision_date``.

    Only quotes with ``day <= decision_date`` are used (a defensive re-filter;
    input should already be aligned by SP 2.15), and quotes older than
    ``lookback_days`` calendar days are excluded. Results are sorted ascending
    by trading day. Missing trading days are not interpolated.

    Raises:
        ValueError: If ``lookback_days`` is negative.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must be non-negative.")
    window_start = decision_date - timedelta(days=lookback_days)
    ordered = sorted(
        (quote for quote in quotes if window_start <= quote.day <= decision_date),
        key=lambda quote: quote.day,
    )
    return tuple(quote.close for quote in ordered)


def extract_price_window(
    quotes: Sequence[DailyQuote],
    decision_date: date,
    config: WindowConfig,
) -> PriceWindowResult:
    """Extract a gated price window for ``decision_date`` (SP 2.16).

    Combines :func:`window_closes` with the configuration's minimum-observation
    gate into a single :class:`PriceWindowResult`.
    """
    closes = window_closes(quotes, decision_date, config.lookback_days)
    return PriceWindowResult(
        decision_date=decision_date,
        closes=closes,
        min_observations=config.min_observations,
    )


def observation_count(values: Sequence[float | None]) -> int:
    """Return the number of non-missing observations in ``values``.

    A ``None`` entry is a missing observation and is not counted.
    """
    return sum(1 for value in values if value is not None)


def has_min_observations(values: Sequence[float | None], min_observations: int) -> bool:
    """Return whether ``values`` holds at least ``min_observations`` entries.

    Raises:
        ValueError: If ``min_observations`` is negative.
    """
    if min_observations < 0:
        raise ValueError("min_observations must be non-negative.")
    return observation_count(values) >= min_observations


def safe_sum(
    values: Sequence[float | None],
    min_observations: int,
) -> float | None:
    """Return the sum of non-missing values, or ``None`` below the gate."""
    if not has_min_observations(values, min_observations):
        return None
    return fsum(value for value in values if value is not None)


def safe_mean(
    values: Sequence[float | None],
    min_observations: int,
) -> float | None:
    """Return the mean of non-missing values, or ``None`` below the gate."""
    if not has_min_observations(values, min_observations):
        return None
    present = [value for value in values if value is not None]
    return fmean(present)


def safe_std(
    values: Sequence[float | None],
    min_observations: int,
    *,
    sample: bool = True,
) -> float | None:
    """Return the standard deviation of non-missing values, or ``None`` below gate.

    ``sample=True`` uses the sample standard deviation (n-1 denominator);
    ``sample=False`` the population standard deviation. Fewer than two
    observations yields ``None`` because a standard deviation is undefined.
    """
    if not has_min_observations(values, min_observations):
        return None
    present = [value for value in values if value is not None]
    count = len(present)
    if count < 2:
        return None
    mean = fmean(present)
    denominator = count - 1 if sample else count
    variance = fsum((value - mean) ** 2 for value in present) / denominator
    return sqrt(variance)


def consecutive_returns(values: Sequence[float | None]) -> tuple[float, ...]:
    """Return simple returns between adjacent non-missing observations.

    A return is ``next / previous - 1`` computed only between consecutive
    non-missing values, so a missing observation is skipped rather than treated
    as a zero return. A previous value that is not positive is skipped to avoid
    a division by zero or an undefined ratio.

    Returns:
        An empty tuple when fewer than two non-missing values are present.
    """
    present = [value for value in values if value is not None]
    returns: list[float] = []
    for index in range(len(present) - 1):
        previous = present[index]
        if previous <= 0:
            continue
        returns.append(present[index + 1] / previous - 1.0)
    return tuple(returns)


def max_drawdown(values: Sequence[float | None]) -> float | None:
    """Return the maximum drawdown (a non-positive fraction) of a series.

    The drawdown at each point is ``value / running_peak - 1``; the returned
    value is the most negative drawdown observed. ``None`` is returned when
    fewer than two non-missing values are present or the running peak is not
    positive.
    """
    present = [value for value in values if value is not None]
    if len(present) < 2:
        return None
    peak = present[0]
    worst = 0.0
    for value in present:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = value / peak - 1.0
            if drawdown < worst:
                worst = drawdown
    return worst
