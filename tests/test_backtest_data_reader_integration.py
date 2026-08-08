"""Backtest data reader integration tests (MVP 2 / SP 2.78).

Uses a real PostgreSQL test database (``HARBOR_TEST_DATABASE_URL``) to verify
the storage-backed reader's point-in-time reads (SP 2.9), market isolation
(SP 2.8), data cutoff (SP 2.8/2.12) and the data-readiness precheck failure
paths (SP 2.13). Skipped when ``HARBOR_TEST_DATABASE_URL`` is not set.
"""

import os
import unittest
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text

from harbor.core.backtest_config import BacktestConfig, MarketQuota
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import TradingCalendar
from harbor.core.data_readiness import run_precheck
from harbor.storage.backtest_data_reader import StorageBacktestDataReader
from harbor.storage.fx_repository import FxRepository
from harbor.storage.repositories import Repository

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TEST_DATABASE_URL = os.getenv("HARBOR_TEST_DATABASE_URL")

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_TRUNCATE_TABLES = (
    "securities",
    "daily_quotes",
    "dividends",
    "financials",
    "fundamentals",
    "corporate_actions",
    "action_terms",
    "adjusted_factors",
    "fx_rates",
)


def _ensure_migrated() -> None:
    """Apply the migration chain up to head against the test database."""
    from alembic.config import Config

    from alembic import command

    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
    try:
        command.upgrade(config, "head")
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url


def _reset_database(engine: object) -> None:
    """Empty the research tables so each test starts from a clean slate."""
    with engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(text(f"TRUNCATE {', '.join(_TRUNCATE_TABLES)} RESTART IDENTITY CASCADE"))


def _security(
    market: Market,
    symbol: str,
    *,
    list_date: date = date(2010, 1, 1),
    delist_date: date | None = None,
) -> dict[str, object]:
    return {
        "market": market.value,
        "symbol": symbol,
        "name": symbol,
        "exchange": "TEST",
        "list_date": list_date,
        "delist_date": delist_date,
        "is_active": True,
    }


def _quote(market: Market, symbol: str, day: date, close: float) -> dict[str, object]:
    return {
        "market": market.value,
        "symbol": symbol,
        "date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000_000,
        "adjusted_close": close,
        "source": "test",
    }


def _financial(
    market: Market,
    symbol: str,
    report_date: date,
    *,
    disclosure_date: date | None,
    fiscal_period: str = "FY",
) -> dict[str, object]:
    return {
        "market": market.value,
        "symbol": symbol,
        "report_date": report_date,
        "fiscal_period": fiscal_period,
        "disclosure_date": disclosure_date,
        "roe": 0.2,
        "net_income": 100.0,
        "total_equity": 500.0,
        "revenue": 200.0,
    }


def _fx(from_: str, to: str, day: date, rate: float) -> dict[str, object]:
    return {
        "from_currency": from_,
        "to_currency": to,
        "date": day,
        "rate": rate,
        "source": "test",
        "quality": "official",
    }


def _seed(
    engine: object,
    *,
    securities: tuple[dict[str, object], ...] = (),
    quotes: tuple[dict[str, object], ...] = (),
    financials: tuple[dict[str, object], ...] = (),
    fx_rates: tuple[dict[str, object], ...] = (),
) -> None:
    """Reset the database and seed the requested rows per market."""
    _reset_database(engine)
    for market in (HK, US):
        sec = [row for row in securities if row["market"] == market.value]
        q = [row for row in quotes if row["market"] == market.value]
        fin = [row for row in financials if row["market"] == market.value]
        if not (sec or q or fin):
            continue
        with engine.begin() as connection:  # type: ignore[union-attr]
            repository = Repository(connection)
            if sec:
                repository.upsert_securities(market.value, sec)
            if q:
                repository.upsert_daily_quotes(market.value, q)
            if fin:
                repository.upsert_financials(market.value, fin)
    if fx_rates:
        with engine.begin() as connection:  # type: ignore[union-attr]
            FxRepository(connection).upsert_fx_rates(list(fx_rates))


class _WeekdayCalendar(TradingCalendar):
    """Weekday-only calendar so date-range tests are deterministic."""

    def is_trading_day(self, market: Market, day: date) -> bool:
        return day.weekday() < 5

    def next_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day += timedelta(days=1)
        return day

    def previous_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day -= timedelta(days=1)
        return day

    def trading_days(self, market: Market, start: date, end: date) -> list[date]:
        days: list[date] = []
        cursor = self.next_trading_day(market, start)
        while cursor <= end:
            days.append(cursor)
            cursor = self.next_trading_day(market, cursor + timedelta(days=1))
        return days

    def rebalance_days(self, market: Market, start: date, end: date) -> list[date]:
        return self.trading_days(market, start, end)


_CALENDAR = _WeekdayCalendar()


def _config(market: Market, base: Currency) -> BacktestConfig:
    return BacktestConfig(
        markets=(market,),
        market_quotas=(MarketQuota(market=market, target_count=1, weight=1.0),),
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        base_currency=base,
    )


@unittest.skipUnless(_TEST_DATABASE_URL, "HARBOR_TEST_DATABASE_URL is not set")
class StorageReaderIntegrationTests(unittest.TestCase):
    """SP 2.78: point-in-time, market isolation, cutoff and precheck paths."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(_TEST_DATABASE_URL)
        _ensure_migrated()

    def setUp(self) -> None:
        _reset_database(self.engine)

    def _reader(self) -> StorageBacktestDataReader:
        connection = self.engine.connect()
        self.addCleanup(connection.close)
        return StorageBacktestDataReader(connection)

    def test_fundamentals_are_point_in_time(self) -> None:
        _seed(
            self.engine,
            securities=(_security(HK, "0001.HK"),),
            financials=(
                _financial(HK, "0001.HK", date(2023, 12, 31), disclosure_date=date(2023, 12, 20)),
                _financial(HK, "0001.HK", date(2024, 3, 31), disclosure_date=date(2024, 4, 10)),
                _financial(HK, "0001.HK", date(2024, 6, 30), disclosure_date=None),
            ),
        )
        reader = self._reader()
        # Only the report disclosed on or before the as-of date is visible.
        before = reader.fundamentals(HK, "0001.HK", date(2024, 4, 9))
        self.assertEqual([r.report_date for r in before], [date(2023, 12, 31)])
        # The later-disclosed report becomes visible exactly at its disclosure date.
        after = reader.fundamentals(HK, "0001.HK", date(2024, 4, 10))
        self.assertEqual([r.report_date for r in after], [date(2023, 12, 31), date(2024, 3, 31)])
        # An undated report is never returned (refused, not guessed).
        self.assertEqual(
            [r.report_date for r in reader.fundamentals(HK, "0001.HK", date(2024, 12, 31))],
            [date(2023, 12, 31), date(2024, 3, 31)],
        )

    def test_market_isolation(self) -> None:
        _seed(
            self.engine,
            securities=(_security(HK, "0001.HK"), _security(US, "AAPL")),
            quotes=(
                _quote(HK, "0001.HK", date(2024, 1, 2), 50.0),
                _quote(US, "AAPL", date(2024, 1, 2), 100.0),
            ),
        )
        reader = self._reader()
        as_of = date(2024, 6, 1)
        self.assertEqual(reader.list_securities(HK, as_of), ["0001.HK"])
        self.assertEqual(reader.list_securities(US, as_of), ["AAPL"])
        # A symbol queried under the wrong market returns nothing.
        self.assertEqual(reader.daily_quotes(HK, "AAPL", date(2024, 1, 1), date(2024, 1, 31)), ())
        self.assertEqual(
            reader.daily_quotes(US, "0001.HK", date(2024, 1, 1), date(2024, 1, 31)), ()
        )
        # The stock pool never crosses markets.
        pool = reader.stock_pool(HK, as_of, historical_known=True)
        self.assertEqual(pool.symbols, ("0001.HK",))
        self.assertNotIn("AAPL", pool.symbols)

    def test_data_cutoff_for_fx_and_quotes(self) -> None:
        _seed(
            self.engine,
            securities=(_security(HK, "0001.HK"),),
            quotes=(
                _quote(HK, "0001.HK", date(2024, 1, 2), 50.0),
                _quote(HK, "0001.HK", date(2024, 1, 3), 51.0),
                _quote(HK, "0001.HK", date(2024, 1, 4), 52.0),
            ),
            fx_rates=(
                _fx("HKD", "USD", date(2024, 1, 2), 0.128),
                _fx("HKD", "USD", date(2024, 1, 4), 0.130),
            ),
        )
        reader = self._reader()
        # FX is the last-known rate on or before the cutoff; missing -> None (no 1:1).
        self.assertIsNone(reader.fx_rate(HKD, USD, date(2024, 1, 1)))
        self.assertEqual(reader.fx_rate(HKD, USD, date(2024, 1, 2)), 0.128)
        self.assertEqual(reader.fx_rate(HKD, USD, date(2024, 1, 3)), 0.128)
        self.assertEqual(reader.fx_rate(HKD, USD, date(2024, 1, 4)), 0.130)
        # Same-currency conversion needs no stored rate.
        self.assertEqual(reader.fx_rate(HKD, HKD, date(2024, 1, 1)), 1.0)
        # Quotes are bounded by the requested date range.
        quotes = reader.daily_quotes(HK, "0001.HK", date(2024, 1, 3), date(2024, 1, 4))
        self.assertEqual([q.day for q in quotes], [date(2024, 1, 3), date(2024, 1, 4)])

    def _precheck(self, reader: StorageBacktestDataReader, config: BacktestConfig):
        return run_precheck(
            config,
            reader,
            _CALENDAR,
            fx_rate=reader.fx_rate,
            stock_pool=lambda market, as_of: reader.stock_pool(
                market, as_of, historical_known=True
            ),
        )

    def test_precheck_reports_empty_pool(self) -> None:
        _seed(self.engine)
        reader = self._reader()
        report = self._precheck(reader, _config(HK, HKD))
        self.assertTrue(report.has_errors)
        self.assertIn("stock pool is empty", report.readable())

    def test_precheck_reports_missing_quotes(self) -> None:
        _seed(self.engine, securities=(_security(HK, "0001.HK"),))
        reader = self._reader()
        report = self._precheck(reader, _config(HK, HKD))
        self.assertTrue(report.has_errors)
        self.assertIn("no daily quotes in the backtest range", report.readable())

    def test_precheck_reports_missing_fx(self) -> None:
        _seed(
            self.engine,
            securities=(_security(US, "AAPL"),),
            quotes=(_quote(US, "AAPL", date(2024, 1, 2), 100.0),),
        )
        reader = self._reader()
        report = self._precheck(reader, _config(US, HKD))
        self.assertTrue(report.has_errors)
        self.assertIn("missing FX USD->HKD", report.readable())
        self.assertIn("refusing to assume 1:1", report.readable())


if __name__ == "__main__":
    unittest.main()
