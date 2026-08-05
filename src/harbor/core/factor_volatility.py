"""Annualized volatility factor (MVP 2 / SP 2.19).

Computes the realized volatility of a symbol on a decision date from its
aligned price history (SP 2.15) using the history window tools (SP 2.16):

- The observation window is configured with :class:`WindowConfig`
  (``lookback_days`` calendar days ending on the decision date and a minimum
  number of price observations). Only quotes on or before the decision date can
  enter the window (the window extractor re-filters defensively).
- Daily returns are computed between adjacent observed closes, and the daily
  volatility is the population standard deviation of those returns.
- The daily volatility is annualized by ``sqrt(annual_trading_days)``, where
  ``annual_trading_days`` is configured per market (e.g. the market's typical
  trading days per year) — the factor never hardcodes a market's calendar.

When the window has fewer than ``min_observations`` price observations, or
fewer than two daily returns (a standard deviation is undefined), the value is
``None`` so the factor surfaces "insufficient history" as missing rather than a
fabricated number. A flat price series has a legitimate volatility of ``0.0``.

Pure core logic: depends only on the backtest domain types and the history
window tools, and never touches storage or CLI code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import sqrt

from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.history_window import (
    WindowConfig,
    consecutive_returns,
    extract_price_window,
    safe_std,
)


@dataclass(frozen=True)
class VolatilityConfig:
    """Parameters for the annualized volatility factor (SP 2.19).

    ``window`` carries the observation lookback and the minimum number of price
    observations required. ``annual_trading_days`` is the annualization factor,
    supplied per market so HK and US are modeled separately.
    """

    window: WindowConfig = WindowConfig()
    annual_trading_days: int = 252

    def __post_init__(self) -> None:
        if self.annual_trading_days <= 0:
            raise ValueError("annual_trading_days must be positive.")


def annualize_volatility(daily_volatility: float, annual_trading_days: int) -> float:
    """Scale a daily volatility to an annualized basis.

    ``daily_volatility * sqrt(annual_trading_days)``.

    Raises:
        ValueError: If ``annual_trading_days`` is not positive.
    """
    if annual_trading_days <= 0:
        raise ValueError("annual_trading_days must be positive.")
    return daily_volatility * sqrt(annual_trading_days)


@dataclass(frozen=True)
class VolatilityResult:
    """The annualized volatility factor outcome for one symbol.

    ``value`` is the annualized volatility (a fraction, e.g. ``0.25``), or
    ``None`` when the window lacks enough observations. ``daily_volatility`` is
    the population standard deviation of the daily returns; ``returns_count``
    and ``observation_count`` report the number of returns and price
    observations the window contained.
    """

    value: float | None
    daily_volatility: float | None
    returns_count: int
    observation_count: int
    annual_trading_days: int
    decision_date: date


def annualized_volatility_factor(
    quotes: Sequence[DailyQuote],
    decision_date: date,
    *,
    config: VolatilityConfig,
) -> VolatilityResult:
    """Compute the annualized volatility on a decision date (SP 2.19).

    Uses only quotes on or before ``decision_date`` within the configured
    lookback window (SP 2.16). The ``min_observations`` gate applies to price
    observations in the window; a standard deviation additionally requires at
    least two daily returns.

    Args:
        quotes: Price history aligned to the decision date (SP 2.15).
        decision_date: The date the factor is evaluated on.
        config: Window and annualization parameters.
    """
    window = extract_price_window(quotes, decision_date, config.window)
    returns = consecutive_returns(window.closes)
    daily_volatility: float | None = None
    if window.sufficient:
        daily_volatility = safe_std(returns, 2, sample=False)
    value: float | None = None
    if daily_volatility is not None:
        value = annualize_volatility(daily_volatility, config.annual_trading_days)
    return VolatilityResult(
        value=value,
        daily_volatility=daily_volatility,
        returns_count=len(returns),
        observation_count=window.observation_count,
        annual_trading_days=config.annual_trading_days,
        decision_date=decision_date,
    )
