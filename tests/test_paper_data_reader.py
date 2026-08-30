"""Paper data reading tests (MVP 4 / SP 4.12).

Verifies that the paper layer reuses the MVP 2 reader surface (quotes,
corporate actions, FX and calendar) without touching storage, that point-in-time
reads are honored (future quotes excluded), and that missing quotes or FX are
surfaced as notes — never assumed — with the caller able to refuse (never 1:1).
"""

import unittest
from datetime import date, timedelta

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    BacktestDataReader,
    DailyQuote,
    EntitlementEvent,
    FxRateRecord,
    TradingCalendar,
)
from harbor.core.paper_data_reader import (
    PaperDataAvailability,
    PaperDataError,
    PaperReaderAdapter,
    assess_paper_data,
    require_paper_data,
)

_HKD = Currency.HKD
_USD = Currency.USD


def _quote(day: date, close: float) -> DailyQuote:
    return DailyQuote(
        market=Market.HK,
        symbol="0001.HK",
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
        adjusted_close=close,
    )


class _NaiveReader(BacktestDataReader):
    """A naive MVP 2 reader that ignores query bounds (defense-in-depth test).

    Returns every quote/action it holds regardless of start/end, so the paper
    layer's own point-in-time filtering is what keeps future data out.
    """

    def __init__(self, quotes: tuple[DailyQuote, ...] = ()) -> None:
        self._quotes = quotes

    def list_securities(self, market: Market, as_of: date) -> list[str]:
        return []

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> tuple[DailyQuote, ...]:
        return self._quotes

    def dividends(self, market: Market, symbol: str, start: date, end: date) -> tuple[object, ...]:
        return ()

    def fundamentals(self, market: Market, symbol: str, as_of: date) -> tuple[object, ...]:
        return ()

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> tuple[EntitlementEvent, ...]:
        return ()

    def adjustment_factors(
        self, market: Market, symbol: str, start: date, end: date
    ) -> tuple[object, ...]:
        return ()


class _WeekdayCalendar(TradingCalendar):
    """A weekday-only calendar used to flag non-trading days."""

    def is_trading_day(self, market: Market, day: date) -> bool:
        return day.weekday() < 5

    def next_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day = day + timedelta(days=1)
        return day

    def previous_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day = day - timedelta(days=1)
        return day

    def trading_days(self, market: Market, start: date, end: date) -> list[date]:
        day = start
        days: list[date] = []
        while day <= end:
            if self.is_trading_day(market, day):
                days.append(day)
            day = day + timedelta(days=1)
        return days

    def rebalance_days(self, market: Market, start: date, end: date) -> list[date]:
        return self.trading_days(market, start, end)


def _adapter(
    quotes: tuple[DailyQuote, ...] = (),
    fx: FxRateRecord | None = None,
) -> PaperReaderAdapter:
    """Return a PaperReaderAdapter over a naive reader plus a fixed FX record."""
    return PaperReaderAdapter(
        _NaiveReader(quotes),
        fx_rate_with_date=lambda _from, _to, _as_of: fx,
    )


class AssessPaperDataTests(unittest.TestCase):
    """Point-in-time availability assessment (SP 4.12)."""

    def test_same_currency_with_quotes_is_ok(self) -> None:
        reader = _adapter(quotes=(_quote(date(2026, 1, 2), 50.0),))
        availability = assess_paper_data(
            reader=reader,
            market=Market.HK,
            symbol="0001.HK",
            as_of=date(2026, 1, 2),
            quote_currency=_HKD,
            base_currency=_HKD,
        )
        self.assertTrue(availability.ok)
        self.assertTrue(availability.has_quotes)
        self.assertTrue(availability.has_fx)
        self.assertEqual(availability.latest_close, 50.0)
        self.assertEqual(availability.fx_rate, 1.0)
        self.assertEqual(availability.notes, ())
        self.assertIn("ok", availability.readable())

    def test_cross_currency_with_fx_is_ok(self) -> None:
        fx = FxRateRecord(_USD, _HKD, 7.8, date(2026, 1, 2))
        reader = _adapter(quotes=(_quote(date(2026, 1, 2), 100.0),), fx=fx)
        availability = assess_paper_data(
            reader=reader,
            market=Market.US,
            symbol="AAPL",
            as_of=date(2026, 1, 2),
            quote_currency=_USD,
            base_currency=_HKD,
        )
        self.assertTrue(availability.ok)
        self.assertEqual(availability.fx_rate, 7.8)

    def test_missing_quotes_surfaced(self) -> None:
        reader = _adapter(quotes=())
        availability = assess_paper_data(
            reader=reader,
            market=Market.HK,
            symbol="0001.HK",
            as_of=date(2026, 1, 2),
            quote_currency=_HKD,
            base_currency=_HKD,
        )
        self.assertFalse(availability.ok)
        self.assertFalse(availability.has_quotes)
        self.assertIn("no quotes on or before", availability.notes[0])

    def test_missing_fx_refuses_1_to_1(self) -> None:
        reader = _adapter(quotes=(_quote(date(2026, 1, 2), 100.0),), fx=None)
        availability = assess_paper_data(
            reader=reader,
            market=Market.US,
            symbol="AAPL",
            as_of=date(2026, 1, 2),
            quote_currency=_USD,
            base_currency=_HKD,
        )
        self.assertFalse(availability.ok)
        self.assertTrue(availability.has_quotes)
        self.assertFalse(availability.has_fx)
        self.assertIn("refusing to assume 1:1", availability.notes[0])

    def test_future_quote_excluded(self) -> None:
        reader = _adapter(quotes=(_quote(date(2026, 1, 1), 10.0), _quote(date(2026, 1, 3), 99.0)))
        availability = assess_paper_data(
            reader=reader,
            market=Market.HK,
            symbol="0001.HK",
            as_of=date(2026, 1, 2),
            quote_currency=_HKD,
            base_currency=_HKD,
        )
        self.assertTrue(availability.ok)
        self.assertEqual(availability.latest_close, 10.0)
        self.assertEqual(availability.latest_quote_date, date(2026, 1, 1))

    def test_non_trading_day_flagged_with_calendar(self) -> None:
        reader = _adapter(quotes=(_quote(date(2026, 1, 3), 50.0),))
        availability = assess_paper_data(
            reader=reader,
            market=Market.HK,
            symbol="0001.HK",
            as_of=date(2026, 1, 3),
            quote_currency=_HKD,
            base_currency=_HKD,
            calendar=_WeekdayCalendar(),
        )
        self.assertTrue(availability.ok)
        self.assertIn("not a trading day", availability.notes[0])


class RequirePaperDataTests(unittest.TestCase):
    """The raising guard (SP 4.12)."""

    def test_ok_passes(self) -> None:
        availability = PaperDataAvailability(
            market=Market.HK,
            symbol="0001.HK",
            as_of=date(2026, 1, 2),
            quote_currency=_HKD,
            base_currency=_HKD,
            has_quotes=True,
            has_fx=True,
        )
        require_paper_data(availability)

    def test_unavailable_raises(self) -> None:
        availability = PaperDataAvailability(
            market=Market.US,
            symbol="AAPL",
            as_of=date(2026, 1, 2),
            quote_currency=_USD,
            base_currency=_HKD,
            has_quotes=True,
            has_fx=False,
            notes=("missing FX USD->HKD on 2026-01-02; refusing to assume 1:1.",),
        )
        with self.assertRaises(PaperDataError) as ctx:
            require_paper_data(availability)
        self.assertIn("refusing to assume 1:1", str(ctx.exception))


class PaperReaderAdapterTests(unittest.TestCase):
    """The adapter reuses the MVP 2 reader + injected FX (SP 4.12)."""

    def test_delegates_quotes_and_fx(self) -> None:
        reader = _adapter(quotes=(_quote(date(2026, 1, 2), 50.0),), fx=None)
        quotes = reader.daily_quotes(Market.HK, "0001.HK", date(2026, 1, 1), date(2026, 1, 2))
        self.assertEqual(len(quotes), 1)
        self.assertEqual(
            reader.corporate_actions(Market.HK, "0001.HK", date(2026, 1, 1), date(2026, 1, 2)), ()
        )
        self.assertIsNone(reader.fx_rate_with_date(_USD, _HKD, date(2026, 1, 2)))

    def test_same_currency_never_calls_fx(self) -> None:
        called = []

        def fx_accessor(_from: Currency, _to: Currency, _as_of: date) -> FxRateRecord | None:
            called.append((_from, _to))
            return None

        reader = PaperReaderAdapter(
            _NaiveReader((_quote(date(2026, 1, 2), 50.0),)),
            fx_rate_with_date=fx_accessor,
        )
        availability = assess_paper_data(
            reader=reader,
            market=Market.HK,
            symbol="0001.HK",
            as_of=date(2026, 1, 2),
            quote_currency=_HKD,
            base_currency=_HKD,
        )
        self.assertTrue(availability.ok)
        self.assertEqual(called, [])
