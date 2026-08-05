"""Factor standardization and direction (MVP 2 / SP 2.22).

Standardizes raw factor values (SP 2.17-2.21) across a set of symbols on a
decision date using quantile, z-score or rank methods, applies the factor's
"higher is better" / "lower is better" direction so that a high standardized
score always means attractive, and optionally winsorizes extreme raw values
before standardizing.

- ``quantile``: maps a value to its empirical cumulative fraction in [0, 1];
  direction ``lower_is_better`` yields ``1 - fraction``.
- ``z-score``: ``(value - mean) / std`` over the non-missing values; direction
  ``lower_is_better`` yields the negation. A zero standard deviation (all
  values equal) yields a neutral ``0.0`` for every symbol.
- ``rank``: an ordinal position with ``1`` meaning the best symbol under the
  configured direction (highest raw value for ``higher_is_better``, lowest for
  ``lower_is_better``).

Missing values (``None``) never participate in standardization and stay
``None``. Ties are broken deterministically by symbol so the output is
replayable. The ``StandardizationConfig`` records the method, direction and
winsorization rule so it can be persisted with the factor snapshot (SP 2.28).

Pure core logic: stdlib only, no storage or CLI dependencies.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import fsum, sqrt
from statistics import fmean


class StandardizationMethod(StrEnum):
    """How raw factor values are standardized (SP 2.22)."""

    QUANTILE = "quantile"
    ZSCORE = "zscore"
    RANK = "rank"


class FactorDirection(StrEnum):
    """Whether a high raw factor value is attractive (SP 2.22).

    ``LOWER_IS_BETTER`` is used e.g. for volatility and drawdown, where a low
    value is desirable; standardization inverts the score so that a high
    standardized score always means attractive.
    """

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


@dataclass(frozen=True)
class StandardizationConfig:
    """The standardization rule for one factor (SP 2.22).

    ``winsorize`` clips raw values to the symmetric empirical tail quantiles
    before standardizing (e.g. ``0.01`` clips to the 1st/99th percentiles). It
    is recorded here so the exact rule can be replayed and reported.
    """

    method: StandardizationMethod = StandardizationMethod.QUANTILE
    direction: FactorDirection = FactorDirection.HIGHER_IS_BETTER
    winsorize: float | None = None

    def __post_init__(self) -> None:
        if self.winsorize is not None and not 0.0 <= self.winsorize < 0.5:
            raise ValueError("winsorize must be in [0.0, 0.5).")

    def describe(self) -> str:
        """Return a human-readable summary of the standardization rule."""
        winsorize = "none" if self.winsorize is None else f"{self.winsorize:.0%} tails"
        return f"{self.method.value}; direction={self.direction.value}; winsorize={winsorize}"


def _present(values: Mapping[str, float | None]) -> dict[str, float]:
    """Return the non-missing values, keeping the mapping order."""
    return {symbol: value for symbol, value in values.items() if value is not None}


def _quantile_bound(sorted_values: list[float], quantile: float) -> float:
    """Return the nearest-rank empirical quantile of a sorted value series."""
    if not sorted_values:
        raise ValueError("cannot compute a quantile of an empty series")
    index = int(round(quantile * (len(sorted_values) - 1)))
    return sorted_values[index]


def _winsorize(present: dict[str, float], winsorize: float) -> dict[str, float]:
    """Clip raw values to the symmetric tail quantiles."""
    if winsorize == 0.0:
        return present
    sorted_values = sorted(present.values())
    lower = _quantile_bound(sorted_values, winsorize)
    upper = _quantile_bound(sorted_values, 1.0 - winsorize)
    return {symbol: min(max(value, lower), upper) for symbol, value in present.items()}


def _quantile(present: dict[str, float]) -> dict[str, float]:
    """Map each value to its empirical cumulative fraction in [0, 1]."""
    ordered = sorted(present, key=lambda symbol: (present[symbol], symbol))
    count = len(ordered)
    if count == 1:
        return {symbol: 0.5 for symbol in ordered}
    return {symbol: index / (count - 1) for index, symbol in enumerate(ordered)}


def _zscore(present: dict[str, float]) -> dict[str, float]:
    """Map each value to its z-score over the non-missing values."""
    values = list(present.values())
    mean = fmean(values)
    variance = fsum((value - mean) ** 2 for value in values) / len(values)
    std = sqrt(variance)
    if std == 0.0:
        return {symbol: 0.0 for symbol in present}
    return {symbol: (value - mean) / std for symbol, value in present.items()}


def _rank(
    present: dict[str, float],
    direction: FactorDirection,
) -> dict[str, int]:
    """Rank symbols ordinally with ``1`` meaning best under ``direction``."""
    ordered = sorted(present, key=lambda symbol: (present[symbol], symbol))
    if direction is FactorDirection.HIGHER_IS_BETTER:
        ordered = list(reversed(ordered))
    return {symbol: index + 1 for index, symbol in enumerate(ordered)}


def _invert(
    standardized: dict[str, float],
    method: StandardizationMethod,
) -> dict[str, float]:
    """Invert quantile/z-score so a high score means attractive."""
    if method is StandardizationMethod.ZSCORE:
        return {symbol: -value for symbol, value in standardized.items()}
    return {symbol: 1.0 - value for symbol, value in standardized.items()}


def standardize_factor(
    values: Mapping[str, float | None],
    *,
    config: StandardizationConfig,
) -> dict[str, float | None]:
    """Standardize per-symbol factor values on a decision date (SP 2.22).

    Missing values stay ``None`` and never influence the statistics of the
    non-missing values. Winsorization, when configured, clips raw values to the
    symmetric tail quantiles before standardizing. For ``rank``, ``1`` marks
    the best symbol under the configured direction; for ``quantile`` and
    ``z-score`` the direction is applied so a high score means attractive.

    Args:
        values: Raw factor value per symbol, ``None`` meaning unavailable.
        config: The standardization rule (method, direction, winsorization).
    """
    present = _present(values)
    result: dict[str, float | None] = {symbol: None for symbol in values}
    if not present:
        return result
    if config.winsorize is not None:
        present = _winsorize(present, config.winsorize)
    if config.method is StandardizationMethod.RANK:
        ranked = _rank(present, config.direction)
        for symbol, rank in ranked.items():
            result[symbol] = rank
        return result
    if config.method is StandardizationMethod.QUANTILE:
        standardized = _quantile(present)
    else:
        standardized = _zscore(present)
    if config.direction is FactorDirection.LOWER_IS_BETTER:
        standardized = _invert(standardized, config.method)
    for symbol, score in standardized.items():
        result[symbol] = score
    return result
