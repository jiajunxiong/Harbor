"""Factor input alignment (MVP 2 / SP 2.15).

Aligns prices, dividends, financials and FX rates to a decision date while
preserving each input's actual availability date. Every aligned input is
guaranteed to have been knowable on or before the decision date (SP 2.9): a
quote is knowable on its own trading day, a dividend on its ex-date, a
fundamental on its disclosure date and an FX rate on its rate date. A record
with an unknown availability date is refused rather than silently used,
because assuming availability is the most common source of look-ahead bias.

This is the input layer the factor computations (SP 2.17-2.21) and the rolling
window tools (SP 2.16) build on. The module is pure core logic: it depends
only on the backtest domain types, the reader contract and the point-in-time
rules, and never touches storage or CLI code.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
    FxRateRecord,
)
from harbor.core.point_in_time import filter_available


def align_price_history(
    quotes: Sequence[DailyQuote],
    decision_date: date,
    lookback_days: int,
) -> tuple[DailyQuote, ...]:
    """Return quotes knowable on or before ``decision_date`` within a window.

    A quote is knowable on its own ``day`` (SP 2.9), so quotes dated after the
    decision date are excluded. Only quotes on or after ``decision_date`` minus
    ``lookback_days`` are kept. Results are sorted ascending by day so rolling
    window tools (SP 2.16) can consume them directly.

    Raises:
        ValueError: If ``lookback_days`` is negative.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must be non-negative.")
    window_start = decision_date - timedelta(days=lookback_days)
    return tuple(
        sorted(
            (quote for quote in quotes if window_start <= quote.day <= decision_date),
            key=lambda quote: quote.day,
        )
    )


def latest_price(
    quotes: Sequence[DailyQuote],
    decision_date: date,
) -> DailyQuote | None:
    """Return the most recent quote on or before ``decision_date``.

    Quotes dated after the decision date are ignored. Returns ``None`` when no
    quote is knowable on or before the decision date.
    """
    available = [quote for quote in quotes if quote.day <= decision_date]
    if not available:
        return None
    return max(available, key=lambda quote: quote.day)


def align_dividends(
    dividends: Sequence[Dividend],
    decision_date: date,
    lookback_days: int,
) -> tuple[Dividend, ...]:
    """Return dividends with an ex-date on or before ``decision_date``.

    The ex-date is the availability date (SP 2.9). Both regular and special
    dividends are retained so callers (e.g. the dividend-yield factor, SP 2.17)
    decide how to treat special payments. Only ex-dates on or after
    ``decision_date`` minus ``lookback_days`` are kept; results are sorted
    ascending by ex-date.

    Raises:
        ValueError: If ``lookback_days`` is negative.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must be non-negative.")
    window_start = decision_date - timedelta(days=lookback_days)
    return tuple(
        sorted(
            (
                dividend
                for dividend in dividends
                if window_start <= dividend.ex_date <= decision_date
            ),
            key=lambda dividend: dividend.ex_date,
        )
    )


def align_fundamental(
    records: Sequence[FundamentalRecord],
    decision_date: date,
) -> FundamentalRecord | None:
    """Return the latest report knowable on or before ``decision_date``.

    Only records with a known ``available_on`` on or before the decision date
    are candidates (SP 2.9); an undated report is refused rather than dated by
    guess. Among the available reports the latest by report date (then fiscal
    period) is returned, or ``None`` when none is available.
    """
    available = filter_available(records, decision_date)
    if not available:
        return None
    return max(available, key=lambda record: (record.report_date, record.fiscal_period))


def align_fx_rate(
    fx_accessor: Callable[[Currency, Currency, date], FxRateRecord | None],
    from_currency: Currency,
    to_currency: Currency,
    decision_date: date,
) -> FxRateRecord | None:
    """Return the FX rate knowable on or before ``decision_date`` with its date.

    ``None`` means no rate is available; the caller must refuse the conversion
    rather than assume 1:1 (SP 2.12). A rate dated after the decision date is
    rejected as a look-ahead guard.

    Raises:
        ValueError: If the accessor returns a rate dated after the decision
            date.
    """
    record = fx_accessor(from_currency, to_currency, decision_date)
    if record is not None and record.date > decision_date:
        raise ValueError(
            f"FX rate dated {record.date.isoformat()} is not knowable on "
            f"{decision_date.isoformat()}."
        )
    return record


@dataclass(frozen=True)
class FactorInputSnapshot:
    """All factor inputs for one symbol aligned to a decision date (SP 2.15).

    Every input is guaranteed to have been knowable on or before
    ``decision_date``, and its actual availability date is preserved:
    ``price_history`` and ``latest_price`` carry each quote's ``day``,
    ``dividends`` carry each ``ex_date``, ``fundamental`` carries its
    ``available_on`` and ``fx`` carries its ``date``.
    """

    market: Market
    symbol: str
    decision_date: date
    price_history: tuple[DailyQuote, ...]
    latest_price: DailyQuote | None
    dividends: tuple[Dividend, ...]
    fundamental: FundamentalRecord | None
    fx: FxRateRecord | None


def build_factor_input_snapshot(
    market: Market,
    symbol: str,
    decision_date: date,
    reader: BacktestDataReader,
    *,
    fx_accessor: Callable[[Currency, Currency, date], FxRateRecord | None],
    quote_currency: Currency,
    to_currency: Currency,
    lookback_days: int = 365,
) -> FactorInputSnapshot:
    """Build the aligned factor inputs for one symbol on a decision date.

    Fetches quotes, dividends, fundamentals and FX through the reader and
    aligns them to ``decision_date`` while preserving each input's availability
    date. When ``quote_currency`` equals ``to_currency`` a synthetic 1.0 rate
    dated on the decision date is recorded and the FX accessor is not called;
    otherwise a missing rate surfaces as ``None`` so the caller refuses rather
    than assumes 1:1 (SP 2.12).

    Raises:
        ValueError: If ``lookback_days`` is negative.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must be non-negative.")
    window_start = decision_date - timedelta(days=lookback_days)
    price_history = align_price_history(
        reader.daily_quotes(market, symbol, window_start, decision_date),
        decision_date,
        lookback_days,
    )
    dividends = align_dividends(
        reader.dividends(market, symbol, window_start, decision_date),
        decision_date,
        lookback_days,
    )
    fundamental = align_fundamental(
        reader.fundamentals(market, symbol, decision_date),
        decision_date,
    )
    if quote_currency is to_currency:
        fx: FxRateRecord | None = FxRateRecord(quote_currency, to_currency, 1.0, decision_date)
    else:
        fx = align_fx_rate(fx_accessor, quote_currency, to_currency, decision_date)
    return FactorInputSnapshot(
        market=market,
        symbol=symbol,
        decision_date=decision_date,
        price_history=price_history,
        latest_price=latest_price(price_history, decision_date),
        dividends=dividends,
        fundamental=fundamental,
        fx=fx,
    )
