"""Factor input alignment tests (MVP 2 / SP 2.15).

Verifies that prices, dividends, financials and FX rates are aligned to a
decision date while preserving each input's actual availability date, and that
records which were not knowable on or before the decision date (future-dated or
undated) are excluded or refused rather than silently used.
"""

import unittest
from collections.abc import Callable, Sequence
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
    FxRateRecord,
)
from harbor.core.factor_alignment import (
    align_dividends,
    align_fundamental,
    align_fx_rate,
    align_price_history,
    build_factor_input_snapshot,
    latest_price,
)
from harbor.core.point_in_time import filter_available

_SYMBOL = "0005.HK"


def _quote(market: Market, symbol: str, day: date, close: float = 100.0) -> DailyQuote:
    return DailyQuote(market, symbol, day, close, close, close, close, 1_000_000, close)


def _dividend(
    market: Market,
    symbol: str,
    ex_date: date,
    amount: float = 1.0,
    is_special: bool = False,
) -> Dividend:
    return Dividend(
        market,
        symbol,
        amount,
        Currency.HKD,
        ex_date,
        record_date=None,
        payment_date=None,
        is_special=is_special,
    )


def _fundamental(
    market: Market,
    symbol: str,
    report_date: date,
    available_on: date | None,
    roe: float = 0.1,
) -> FundamentalRecord:
    return FundamentalRecord(
        market,
        symbol,
        report_date,
        str(report_date.year),
        available_on,
        roe=roe,
        net_income=1.0e9,
        total_equity=1.0e10,
        revenue=5.0e9,
    )


def _fx_accessor(
    rate: float,
    rate_date: date,
) -> Callable[[Currency, Currency, date], FxRateRecord | None]:
    """Return an accessor yielding a fixed rate dated on ``rate_date``."""

    def accessor(
        from_currency: Currency,
        to_currency: Currency,
        as_of: date,
    ) -> FxRateRecord | None:
        return FxRateRecord(from_currency, to_currency, rate, rate_date)

    return accessor


def _raising_fx(
    from_currency: Currency,
    to_currency: Currency,
    as_of: date,
) -> FxRateRecord | None:
    raise AssertionError("fx accessor must not be called for a same-currency pair")


def _missing_fx(
    from_currency: Currency,
    to_currency: Currency,
    as_of: date,
) -> FxRateRecord | None:
    return None


class _FakeReader(BacktestDataReader):
    """Minimal reader serving fixed quote/dividend/fundamental records."""

    def __init__(
        self,
        quotes: Sequence[DailyQuote],
        dividends: Sequence[Dividend],
        fundamentals: Sequence[FundamentalRecord],
    ) -> None:
        self._quotes = quotes
        self._dividends = dividends
        self._fundamentals = fundamentals

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        return ()

    def daily_quotes(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[DailyQuote]:
        return tuple(quote for quote in self._quotes if start <= quote.day <= end)

    def dividends(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Dividend]:
        return tuple(dividend for dividend in self._dividends if start <= dividend.ex_date <= end)

    def fundamentals(
        self,
        market: Market,
        symbol: str,
        as_of: date,
    ) -> Sequence[FundamentalRecord]:
        return filter_available(self._fundamentals, as_of)

    def corporate_actions(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[object]:
        return ()

    def adjustment_factors(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[object]:
        return ()


class AlignPriceHistoryTests(unittest.TestCase):
    """Verify price history alignment to a decision date (SP 2.15)."""

    def test_excludes_quotes_after_decision_date(self) -> None:
        decision = date(2026, 3, 31)
        quotes = [
            _quote(Market.HK, _SYMBOL, date(2026, 3, 30), 100.0),
            _quote(Market.HK, _SYMBOL, date(2026, 4, 1), 101.0),
        ]
        aligned = align_price_history(quotes, decision, 365)
        self.assertEqual([quote.day for quote in aligned], [date(2026, 3, 30)])

    def test_respects_lookback_window(self) -> None:
        decision = date(2026, 3, 31)
        quotes = [
            _quote(Market.HK, _SYMBOL, date(2025, 1, 1), 90.0),
            _quote(Market.HK, _SYMBOL, date(2026, 3, 1), 99.0),
            _quote(Market.HK, _SYMBOL, date(2026, 3, 31), 101.0),
        ]
        aligned = align_price_history(quotes, decision, 30)
        self.assertEqual(
            [quote.day for quote in aligned],
            [date(2026, 3, 1), date(2026, 3, 31)],
        )

    def test_sorts_ascending(self) -> None:
        decision = date(2026, 3, 31)
        quotes = [
            _quote(Market.HK, _SYMBOL, date(2026, 3, 30), 102.0),
            _quote(Market.HK, _SYMBOL, date(2026, 3, 1), 99.0),
        ]
        aligned = align_price_history(quotes, decision, 365)
        self.assertEqual(
            [quote.day for quote in aligned],
            [date(2026, 3, 1), date(2026, 3, 30)],
        )

    def test_empty_when_no_quotes_in_window(self) -> None:
        quotes = [_quote(Market.HK, _SYMBOL, date(2025, 1, 1), 90.0)]
        self.assertEqual(align_price_history(quotes, date(2026, 3, 31), 30), ())

    def test_rejects_negative_lookback(self) -> None:
        with self.assertRaisesRegex(ValueError, "lookback"):
            align_price_history((), date(2026, 3, 31), -1)


class LatestPriceTests(unittest.TestCase):
    """Verify the latest-known-price helper (SP 2.15)."""

    def test_returns_most_recent_on_or_before_decision_date(self) -> None:
        quotes = [
            _quote(Market.HK, _SYMBOL, date(2026, 3, 1), 99.0),
            _quote(Market.HK, _SYMBOL, date(2026, 3, 30), 102.0),
        ]
        result = latest_price(quotes, date(2026, 3, 31))
        self.assertIsNotNone(result)
        self.assertEqual(result.day, date(2026, 3, 30))
        self.assertEqual(result.close, 102.0)

    def test_ignores_future_quotes(self) -> None:
        quotes = [
            _quote(Market.HK, _SYMBOL, date(2026, 4, 1), 105.0),
            _quote(Market.HK, _SYMBOL, date(2026, 3, 30), 102.0),
        ]
        result = latest_price(quotes, date(2026, 3, 31))
        self.assertIsNotNone(result)
        self.assertEqual(result.day, date(2026, 3, 30))

    def test_none_when_no_quote_on_or_before(self) -> None:
        quotes = [_quote(Market.HK, _SYMBOL, date(2026, 4, 1), 105.0)]
        self.assertIsNone(latest_price(quotes, date(2026, 3, 31)))


class AlignDividendsTests(unittest.TestCase):
    """Verify dividend alignment to a decision date (SP 2.15)."""

    def test_filters_by_ex_date_window_and_keeps_special(self) -> None:
        decision = date(2026, 3, 31)
        special = _dividend(Market.HK, _SYMBOL, date(2026, 3, 1), is_special=True)
        regular = _dividend(Market.HK, _SYMBOL, date(2026, 3, 15))
        future = _dividend(Market.HK, _SYMBOL, date(2026, 4, 1))
        old = _dividend(Market.HK, _SYMBOL, date(2025, 1, 1))
        aligned = align_dividends([old, future, regular, special], decision, 60)
        self.assertEqual(
            [dividend.ex_date for dividend in aligned],
            [date(2026, 3, 1), date(2026, 3, 15)],
        )
        self.assertTrue(aligned[0].is_special)

    def test_sorts_by_ex_date(self) -> None:
        later = _dividend(Market.HK, _SYMBOL, date(2026, 3, 15))
        earlier = _dividend(Market.HK, _SYMBOL, date(2026, 2, 1))
        aligned = align_dividends([later, earlier], date(2026, 3, 31), 365)
        self.assertEqual(
            [dividend.ex_date for dividend in aligned],
            [date(2026, 2, 1), date(2026, 3, 15)],
        )

    def test_empty_when_none_in_window(self) -> None:
        dividend = _dividend(Market.HK, _SYMBOL, date(2026, 4, 1))
        self.assertEqual(align_dividends([dividend], date(2026, 3, 31), 365), ())

    def test_rejects_negative_lookback(self) -> None:
        with self.assertRaisesRegex(ValueError, "lookback"):
            align_dividends((), date(2026, 3, 31), -1)


class AlignFundamentalTests(unittest.TestCase):
    """Verify fundamental alignment uses disclosure availability (SP 2.15)."""

    def test_returns_latest_available_report(self) -> None:
        decision = date(2026, 3, 31)
        old = _fundamental(Market.HK, _SYMBOL, date(2024, 12, 31), date(2025, 3, 15), roe=0.05)
        recent = _fundamental(Market.HK, _SYMBOL, date(2025, 12, 31), date(2026, 3, 10), roe=0.15)
        result = align_fundamental([old, recent], decision)
        self.assertIs(result, recent)
        self.assertEqual(result.report_date, date(2025, 12, 31))
        self.assertEqual(result.available_on, date(2026, 3, 10))

    def test_refuses_undated_report(self) -> None:
        undated = _fundamental(Market.HK, _SYMBOL, date(2025, 12, 31), None, roe=0.15)
        self.assertIsNone(align_fundamental([undated], date(2026, 3, 31)))

    def test_excludes_future_disclosure(self) -> None:
        future = _fundamental(Market.HK, _SYMBOL, date(2025, 12, 31), date(2026, 4, 1), roe=0.15)
        self.assertIsNone(align_fundamental([future], date(2026, 3, 31)))

    def test_none_when_nothing_available(self) -> None:
        self.assertIsNone(align_fundamental((), date(2026, 3, 31)))


class AlignFxRateTests(unittest.TestCase):
    """Verify FX alignment preserves the rate date (SP 2.15)."""

    def test_returns_record_with_date(self) -> None:
        record = align_fx_rate(
            _fx_accessor(0.128, date(2026, 3, 1)),
            Currency.HKD,
            Currency.USD,
            date(2026, 3, 31),
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.rate, 0.128)
        self.assertEqual(record.date, date(2026, 3, 1))

    def test_none_when_unavailable(self) -> None:
        self.assertIsNone(align_fx_rate(_missing_fx, Currency.HKD, Currency.USD, date(2026, 3, 31)))

    def test_rejects_future_dated_rate(self) -> None:
        with self.assertRaisesRegex(ValueError, "not knowable"):
            align_fx_rate(
                _fx_accessor(0.128, date(2026, 4, 1)),
                Currency.HKD,
                Currency.USD,
                date(2026, 3, 31),
            )


class FactorInputSnapshotTests(unittest.TestCase):
    """Verify the end-to-end snapshot builder (SP 2.15)."""

    def test_snapshot_preserves_availability_and_excludes_future(self) -> None:
        decision = date(2026, 3, 31)
        quotes = [
            _quote(Market.HK, _SYMBOL, date(2026, 1, 15), 100.0),
            _quote(Market.HK, _SYMBOL, date(2026, 3, 30), 102.0),
            _quote(Market.HK, _SYMBOL, date(2026, 4, 1), 105.0),
        ]
        dividends = [
            _dividend(Market.HK, _SYMBOL, date(2026, 2, 1), 1.0),
            _dividend(Market.HK, _SYMBOL, date(2026, 4, 2), 2.0, is_special=True),
        ]
        fundamentals = [
            _fundamental(Market.HK, _SYMBOL, date(2025, 12, 31), date(2026, 3, 10), roe=0.15),
            _fundamental(Market.HK, _SYMBOL, date(2024, 12, 31), None, roe=0.05),
            _fundamental(Market.HK, _SYMBOL, date(2025, 6, 30), date(2026, 4, 5), roe=0.2),
        ]
        reader = _FakeReader(quotes, dividends, fundamentals)
        snapshot = build_factor_input_snapshot(
            Market.HK,
            _SYMBOL,
            decision,
            reader,
            fx_accessor=_fx_accessor(0.128, date(2026, 3, 1)),
            quote_currency=Currency.HKD,
            to_currency=Currency.USD,
            lookback_days=365,
        )

        self.assertEqual(snapshot.market, Market.HK)
        self.assertEqual(snapshot.symbol, _SYMBOL)
        self.assertEqual(snapshot.decision_date, decision)
        self.assertEqual(
            [quote.day for quote in snapshot.price_history],
            [date(2026, 1, 15), date(2026, 3, 30)],
        )
        self.assertEqual(snapshot.latest_price.day, date(2026, 3, 30))
        self.assertEqual(
            [dividend.ex_date for dividend in snapshot.dividends],
            [date(2026, 2, 1)],
        )
        self.assertIsNotNone(snapshot.fundamental)
        self.assertEqual(snapshot.fundamental.report_date, date(2025, 12, 31))
        self.assertEqual(snapshot.fundamental.available_on, date(2026, 3, 10))
        self.assertIsNotNone(snapshot.fx)
        self.assertEqual(snapshot.fx.rate, 0.128)
        self.assertEqual(snapshot.fx.date, date(2026, 3, 1))

    def test_snapshot_same_currency_does_not_call_fx_accessor(self) -> None:
        reader = _FakeReader((), (), ())
        snapshot = build_factor_input_snapshot(
            Market.US,
            "AAPL",
            date(2026, 3, 31),
            reader,
            fx_accessor=_raising_fx,
            quote_currency=Currency.USD,
            to_currency=Currency.USD,
            lookback_days=365,
        )
        self.assertIsNotNone(snapshot.fx)
        self.assertEqual(snapshot.fx.rate, 1.0)
        self.assertEqual(snapshot.fx.date, date(2026, 3, 31))

    def test_snapshot_missing_fx_surfaces_none(self) -> None:
        reader = _FakeReader((), (), ())
        snapshot = build_factor_input_snapshot(
            Market.HK,
            _SYMBOL,
            date(2026, 3, 31),
            reader,
            fx_accessor=_missing_fx,
            quote_currency=Currency.HKD,
            to_currency=Currency.USD,
            lookback_days=365,
        )
        self.assertIsNone(snapshot.fx)

    def test_snapshot_rejects_negative_lookback(self) -> None:
        reader = _FakeReader((), (), ())
        with self.assertRaisesRegex(ValueError, "lookback"):
            build_factor_input_snapshot(
                Market.HK,
                _SYMBOL,
                date(2026, 3, 31),
                reader,
                fx_accessor=_missing_fx,
                quote_currency=Currency.HKD,
                to_currency=Currency.USD,
                lookback_days=-1,
            )


if __name__ == "__main__":
    unittest.main()
