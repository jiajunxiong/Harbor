"""Drawdown and liquidity factor (MVP 2 / SP 2.20).

Computes three tradability inputs for a symbol on a decision date from its
aligned price history (SP 2.15) and the market calendar:

- ``max_drawdown``: the historical maximum drawdown (a non-positive fraction)
  over the observation window, from the history window tools (SP 2.16).
- ``average_turnover``: the mean daily turnover (``volume * close``) over the
  window, gated by the configured minimum observation count.
- ``suspension_ratio``: the fraction of the market's expected trading days in
  the window with no quote, ``1 - observed / expected``, where expected days
  come from the market's own trading calendar. When the calendar yields no
  expected trading days the ratio is ``None``.

All inputs are point-in-time: only quotes on or before the decision date within
the window are used, so a future-dated quote can never enter. Metrics that lack
enough observations are ``None`` rather than fabricated, so the candidate filter
(SP 2.23) can exclude on insufficient history, low liquidity or long
suspensions.

Pure core logic: depends only on the backtest domain types, the reader-level
calendar contract and the history window tools, and never touches storage or
CLI code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import DailyQuote, TradingCalendar
from harbor.core.history_window import WindowConfig, max_drawdown, safe_mean


def _window_quotes(
    quotes: Sequence[DailyQuote],
    decision_date: date,
    lookback_days: int,
) -> tuple[DailyQuote, ...]:
    """Return quotes on or before ``decision_date`` within the window, sorted."""
    window_start = decision_date - timedelta(days=lookback_days)
    return tuple(
        sorted(
            (quote for quote in quotes if window_start <= quote.day <= decision_date),
            key=lambda quote: quote.day,
        )
    )


@dataclass(frozen=True)
class DrawdownLiquidityConfig:
    """Parameters for the drawdown and liquidity factor (SP 2.20)."""

    window: WindowConfig = WindowConfig()


@dataclass(frozen=True)
class DrawdownLiquidityResult:
    """Drawdown, turnover and suspension metrics for one symbol.

    ``max_drawdown`` is a non-positive fraction (or ``None`` with fewer than
    ``min_observations`` closes). ``average_turnover`` is the mean daily
    ``volume * close`` (or ``None`` below the minimum observation gate).
    ``suspension_ratio`` is the fraction of expected trading days with no
    quote, or ``None`` when the calendar yields no expected trading days.
    """

    max_drawdown: float | None
    average_turnover: float | None
    suspension_ratio: float | None
    observed_days: int
    expected_days: int
    decision_date: date


def drawdown_liquidity_factor(
    market: Market,
    quotes: Sequence[DailyQuote],
    decision_date: date,
    calendar: TradingCalendar,
    *,
    config: DrawdownLiquidityConfig | None = None,
) -> DrawdownLiquidityResult:
    """Compute drawdown, turnover and suspension metrics (SP 2.20).

    Args:
        market: The market whose calendar defines expected trading days.
        quotes: Price history aligned to the decision date (SP 2.15).
        decision_date: The date the factor is evaluated on.
        calendar: The market's trading calendar used for expected trading days.
        config: Optional window parameters.
    """
    if config is None:
        config = DrawdownLiquidityConfig()
    window_quotes = _window_quotes(quotes, decision_date, config.window.lookback_days)
    observed_days = len(window_quotes)
    sufficient = observed_days >= config.window.min_observations

    closes = tuple(quote.close for quote in window_quotes)
    max_dd: float | None = max_drawdown(closes) if sufficient else None

    turnovers = tuple(quote.volume * quote.close for quote in window_quotes)
    average_turnover = safe_mean(turnovers, config.window.min_observations)

    window_start = decision_date - timedelta(days=config.window.lookback_days)
    expected_days = len(calendar.trading_days(market, window_start, decision_date))
    suspension_ratio: float | None = None
    if expected_days > 0:
        suspension_ratio = max(0.0, 1.0 - observed_days / expected_days)

    return DrawdownLiquidityResult(
        max_drawdown=max_dd,
        average_turnover=average_turnover,
        suspension_ratio=suspension_ratio,
        observed_days=observed_days,
        expected_days=expected_days,
        decision_date=decision_date,
    )
