"""Paper data reading (MVP 4 / SP 4.12).

The paper loop reuses the MVP 1/2 data layer for daily quotes, FX rates and
corporate actions without depending on any concrete storage implementation.
:class:`PaperDataReader` is the Protocol the paper layer consumes — it matches
the SP 2.3 ``BacktestDataReader`` surface plus the SP 2.15 ``fx_rate_with_date``
method, so the storage-backed reader satisfies it structurally, and
:class:`PaperReaderAdapter` adapts any plain MVP 2 reader plus an injected FX
accessor.

:func:`assess_paper_data` reports, for one symbol on one day, whether quotes
and the FX rate to the base currency are available. Missing quotes or a
missing FX rate (when the quote currency differs from the base) are surfaced
as notes — never assumed — so the caller can refuse rather than assuming a 1:1
exchange (SP 2.12 / MVP 4 acceptance). The trading calendar (SP 2.11) may also
be supplied to flag non-trading days.

Pure core logic: depends on the backtest interfaces and the equity domain;
never touches storage, services or CLI code.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    BacktestDataReader,
    DailyQuote,
    FxRateRecord,
    TradingCalendar,
)
from harbor.core.equity import EntitlementEvent


class PaperDataReader(Protocol):
    """The MVP 1/2 data-layer surface a paper run may read (SP 4.12).

    Reuses the SP 2.3 / SP 2.15 reader contracts for daily quotes, corporate
    actions and FX rates; the paper layer never depends on a concrete storage
    implementation.
    """

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[DailyQuote]: ...

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[EntitlementEvent]: ...

    def fx_rate_with_date(
        self, from_currency: Currency, to_currency: Currency, as_of: date
    ) -> FxRateRecord | None: ...


class PaperDataError(ValueError):
    """Raised when required paper data is unavailable (SP 4.12)."""


@dataclass(frozen=True)
class PaperDataAvailability:
    """What a paper run knows about one symbol on one day (SP 4.12).

    ``has_quotes`` is true when a quote on or before ``as_of`` is known;
    ``has_fx`` is true when the quote currency equals the base currency or an
    FX rate to the base currency is known. Missing data is recorded in
    ``notes`` (never fabricated).
    """

    market: Market
    symbol: str
    as_of: date
    quote_currency: Currency
    base_currency: Currency
    has_quotes: bool
    has_fx: bool
    latest_close: float | None = None
    latest_quote_date: date | None = None
    fx_rate: float | None = None
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether quotes and FX (where needed) are available."""
        return self.has_quotes and self.has_fx

    def readable(self) -> str:
        """Render the availability as a compact summary."""
        status = "ok" if self.ok else "unavailable"
        lines = [f"{self.market.value}/{self.symbol} on {self.as_of.isoformat()} {status}"]
        if self.latest_close is not None and self.latest_quote_date is not None:
            lines.append(
                f"  latest close {self.latest_close:g} on {self.latest_quote_date.isoformat()}"
            )
        if self.fx_rate is not None:
            lines.append(
                f"  fx {self.quote_currency.value}->{self.base_currency.value} {self.fx_rate:g}"
            )
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


class PaperReaderAdapter:
    """Adapt an MVP 2 reader plus an FX accessor to the paper surface (SP 4.12).

    Lets the paper loop reuse any SP 2.3 ``BacktestDataReader`` (a mock or the
    storage-backed reader) without importing storage directly; FX is injected
    as a ``fx_rate_with_date`` callable (SP 2.15).
    """

    def __init__(
        self,
        reader: BacktestDataReader,
        *,
        fx_rate_with_date: Callable[[Currency, Currency, date], FxRateRecord | None],
    ) -> None:
        self._reader = reader
        self._fx = fx_rate_with_date

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[DailyQuote]:
        """Delegate daily quotes to the wrapped MVP 2 reader."""
        return self._reader.daily_quotes(market, symbol, start, end)

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[EntitlementEvent]:
        """Delegate corporate actions to the wrapped MVP 2 reader."""
        return self._reader.corporate_actions(market, symbol, start, end)

    def fx_rate_with_date(
        self, from_currency: Currency, to_currency: Currency, as_of: date
    ) -> FxRateRecord | None:
        """Delegate FX lookup to the injected accessor."""
        return self._fx(from_currency, to_currency, as_of)


def assess_paper_data(
    *,
    reader: PaperDataReader,
    market: Market,
    symbol: str,
    as_of: date,
    quote_currency: Currency,
    base_currency: Currency,
    lookback_days: int = 5,
    calendar: TradingCalendar | None = None,
) -> PaperDataAvailability:
    """Assess whether a paper read is possible for one symbol on ``as_of`` (SP 4.12).

    Reuses the MVP 2 reader for daily quotes and FX (SP 2.8 / 2.15): the latest
    quote on or before ``as_of`` and the FX rate to the base currency. Missing
    quotes or a missing FX rate (when the quote currency differs from the base)
    are surfaced as notes — never assumed — so the caller can refuse (never
    1:1). ``calendar`` optionally flags non-trading days (SP 2.11).
    """
    start = as_of - timedelta(days=lookback_days)
    quotes = reader.daily_quotes(market, symbol, start, as_of)
    latest: DailyQuote | None = None
    for quote in quotes:
        if quote.day <= as_of and (latest is None or quote.day > latest.day):
            latest = quote
    notes: list[str] = []
    if latest is None:
        notes.append(
            f"no quotes on or before {as_of.isoformat()} (looked back "
            f"{lookback_days} days); symbol is suspended, untradeable or missing."
        )
    fx_rate: float | None
    if quote_currency is base_currency:
        has_fx = True
        fx_rate = 1.0
    else:
        record = reader.fx_rate_with_date(quote_currency, base_currency, as_of)
        has_fx = record is not None
        fx_rate = None if record is None else record.rate
        if not has_fx:
            notes.append(
                f"missing FX {quote_currency.value}->{base_currency.value} on "
                f"{as_of.isoformat()}; refusing to assume 1:1."
            )
    if calendar is not None and not calendar.is_trading_day(market, as_of):
        notes.append(f"{as_of.isoformat()} is not a trading day in {market.value}.")
    return PaperDataAvailability(
        market=market,
        symbol=symbol,
        as_of=as_of,
        quote_currency=quote_currency,
        base_currency=base_currency,
        has_quotes=latest is not None,
        has_fx=has_fx,
        latest_close=None if latest is None else latest.close,
        latest_quote_date=None if latest is None else latest.day,
        fx_rate=fx_rate,
        notes=tuple(notes),
    )


def require_paper_data(availability: PaperDataAvailability) -> None:
    """Raise unless the availability is ok (SP 4.12).

    Raises:
        PaperDataError: If quotes or FX are unavailable for the symbol.
    """
    if not availability.ok:
        raise PaperDataError(
            f"paper data unavailable for {availability.market.value}/"
            f"{availability.symbol} on {availability.as_of.isoformat()}: "
            f"{'; '.join(availability.notes)}."
        )
