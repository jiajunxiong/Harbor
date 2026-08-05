"""SP 2.14: minimal research dataset smoke test.

Loads the MockProvider HK and US datasets plus a fixed FX rate through a
read-only backtest reader and verifies the SP 2.13 data-readiness precheck
reports no blocking errors, so a research backtest can start.

The in-memory reader reuses the exact row-to-domain mappers from the
storage-backed reader (``harbor.storage.backtest_data_reader``), so the
production load path is exercised without a database. A live-PostgreSQL
variant (skipped without ``HARBOR_TEST_DATABASE_URL``) loads the same data and
reads it back through :class:`StorageBacktestDataReader`.
"""

import os
import unittest
import uuid
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from harbor.config import MarketTarget
from harbor.core.backtest_config import BacktestConfig, MarketQuota, RebalanceFrequency
from harbor.core.backtest_domain import Currency, Market, to_market_target
from harbor.core.backtest_interfaces import (
    AdjustmentFactor,
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.data_readiness import PrecheckSeverity, run_precheck
from harbor.core.equity import EntitlementEvent
from harbor.core.ingestion import (
    CorporateActionIngestor,
    DailyQuoteIngestor,
    DividendIngestor,
    FinancialIngestor,
    SecuritiesIngestor,
)
from harbor.core.market_registry import get_market_config
from harbor.core.point_in_time import filter_available
from harbor.core.stock_pool import StockPool, evaluate_stock_pool
from harbor.core.trading_calendar import MarketTradingCalendar
from harbor.infrastructure.data_providers.mock import MockProvider
from harbor.storage.backtest_data_reader import (
    StorageBacktestDataReader,
    _dividend_from_row,
    _entitlements_from_rows,
    _fundamental_from_row,
    _membership_from_row,
    _quote_from_row,
)
from harbor.storage.fx_repository import FxRepository
from harbor.storage.repositories import Repository

_SMOKE_START = date(2024, 1, 1)
_SMOKE_END = date(2025, 12, 31)

#: Fixed research FX rate: number of USD per one HKD (SP 2.12 stand-in).
_FIXED_HKD_TO_USD = 0.128

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TEST_DATABASE_URL = os.getenv("HARBOR_TEST_DATABASE_URL")

#: Weekday-only calendar that exactly matches MockProvider quote generation.
_WEEKDAY_CALENDAR = MarketTradingCalendar({Market.HK: frozenset(), Market.US: frozenset()})


def _smoke_config() -> BacktestConfig:
    """Return a conservative HK + US quarterly research configuration."""
    return BacktestConfig(
        strategy="shareholder-return",
        strategy_version="0.0.1",
        description="SP 2.14 research smoke test",
        markets=(Market.HK, Market.US),
        market_quotas=(
            MarketQuota(market=Market.HK, target_count=5, weight=0.5),
            MarketQuota(market=Market.US, target_count=5, weight=0.5),
        ),
        start_date=_SMOKE_START,
        end_date=_SMOKE_END,
        base_currency=Currency.USD,
        rebalance_frequency=RebalanceFrequency.QUARTERLY,
        initial_capital=1_000_000.0,
    )


def fixed_fx(
    from_currency: Currency,
    to_currency: Currency,
    as_of: date,
) -> float | None:
    """Return a fixed research FX rate, refusing unknown pairs (SP 2.12)."""
    if from_currency is to_currency:
        return 1.0
    if (from_currency, to_currency) == (Currency.HKD, Currency.USD):
        return _FIXED_HKD_TO_USD
    if (from_currency, to_currency) == (Currency.USD, Currency.HKD):
        return 1.0 / _FIXED_HKD_TO_USD
    return None


class MockBacktestDataReader(BacktestDataReader):
    """Read-only BacktestDataReader backed by MockProvider data (SP 2.14).

    Loads the full MockProvider HK/US datasets once and serves immutable domain
    records. Row-to-record mapping reuses the storage reader's mappers so the
    exact production load path is exercised without a database. The reader is
    read-only: loading never mutates the provider data, and every call returns
    freshly built immutable records.
    """

    def __init__(self, start: date, end: date) -> None:
        self._start = start
        self._end = end
        self._provider = MockProvider()
        self._securities: dict[Market, tuple[dict[str, object], ...]] = {}
        self._quotes: dict[tuple[Market, str], tuple[dict[str, object], ...]] = {}
        self._dividends: dict[tuple[Market, str], tuple[dict[str, object], ...]] = {}
        self._financials: dict[tuple[Market, str], tuple[dict[str, object], ...]] = {}
        self._actions: dict[tuple[Market, str], tuple[dict[str, object], ...]] = {}
        self._load()

    def _load(self) -> None:
        """Snapshot the MockProvider datasets for both markets."""
        for market in (Market.HK, Market.US):
            target = to_market_target(market)
            securities = tuple(dict(row) for row in self._provider.list_securities(target))
            self._securities[market] = securities
            for security in securities:
                symbol = str(security["symbol"])
                key = (market, symbol)
                self._quotes[key] = tuple(
                    dict(row)
                    for row in self._provider.fetch_daily_quotes(
                        target, symbol, self._start, self._end
                    )
                )
                self._dividends[key] = tuple(
                    dict(row)
                    for row in self._provider.fetch_dividends(
                        target, symbol, self._start, self._end
                    )
                )
                self._financials[key] = tuple(
                    dict(row) for row in self._provider.fetch_financials(target, symbol)
                )
                self._actions[key] = tuple(
                    dict(row)
                    for row in self._provider.fetch_corporate_actions(
                        target, symbol, self._start, self._end
                    )
                )

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        """Return symbols listed and not yet delisted on ``as_of``."""
        symbols = []
        for row in self._securities[market]:
            if row["list_date"] <= as_of and (
                row["delist_date"] is None or row["delist_date"] >= as_of
            ):
                symbols.append(str(row["symbol"]))
        return tuple(symbols)

    def daily_quotes(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[DailyQuote]:
        return tuple(
            _quote_from_row(row)
            for row in self._quotes[(market, symbol)]
            if start <= row["date"] <= end
        )

    def dividends(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Dividend]:
        return tuple(
            _dividend_from_row(row)
            for row in self._dividends[(market, symbol)]
            if start <= row["ex_date"] <= end
        )

    def fundamentals(
        self,
        market: Market,
        symbol: str,
        as_of: date,
    ) -> Sequence[FundamentalRecord]:
        records = tuple(_fundamental_from_row(row) for row in self._financials[(market, symbol)])
        return filter_available(records, as_of)

    def corporate_actions(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[EntitlementEvent]:
        rows = tuple(
            row for row in self._actions[(market, symbol)] if start <= row["ex_date"] <= end
        )
        return _entitlements_from_rows(rows, ())

    def adjustment_factors(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[AdjustmentFactor]:
        # MockProvider does not generate adjusted factors; none are available.
        return ()

    def stock_pool(self, market: Market, as_of: date) -> StockPool:
        """Evaluate the historical stock pool on ``as_of`` (SP 2.10)."""
        source = get_market_config(to_market_target(market)).stock_pool_source
        memberships = [_membership_from_row(row, source) for row in self._securities[market]]
        return evaluate_stock_pool(
            market,
            as_of,
            memberships,
            source,
            historical_known=True,
        )


class MockResearchSmokeTests(unittest.TestCase):
    """SP 2.14: read-only load of MockProvider HK+US data with fixed FX."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.reader = MockBacktestDataReader(_SMOKE_START, _SMOKE_END)
        cls.config = _smoke_config()
        cls.report = run_precheck(
            cls.config,
            cls.reader,
            _WEEKDAY_CALENDAR,
            fx_rate=fixed_fx,
            stock_pool=cls.reader.stock_pool,
        )

    def test_load_reports_no_blocking_errors(self) -> None:
        self.assertFalse(self.report.has_errors)

    def test_readable_summary_confirms_load(self) -> None:
        summary = self.report.readable()
        self.assertIn("No blocking errors", summary)

    def test_both_markets_load_securities_and_quotes(self) -> None:
        for market in (Market.HK, Market.US):
            symbols = self.reader.list_securities(market, _SMOKE_START)
            self.assertGreaterEqual(len(symbols), 10)
            quotes = self.reader.daily_quotes(market, symbols[0], _SMOKE_START, _SMOKE_END)
            self.assertGreater(len(quotes), 100)

    def test_hk_fx_resolved_with_fixed_rate(self) -> None:
        error_scopes = {finding.scope for finding in self.report.errors}
        self.assertNotIn("HK/fx", error_scopes)
        self.assertEqual(
            fixed_fx(Currency.HKD, Currency.USD, _SMOKE_START),
            _FIXED_HKD_TO_USD,
        )

    def test_undated_fundamentals_surface_as_warnings(self) -> None:
        warnings = [
            finding
            for finding in self.report.findings
            if finding.severity is PrecheckSeverity.WARNING
        ]
        self.assertTrue(
            any("no point-in-time fundamentals" in finding.message for finding in warnings)
        )

    def test_load_is_read_only_and_repeatable(self) -> None:
        first = self.report.readable()
        replayed = run_precheck(
            self.config,
            self.reader,
            _WEEKDAY_CALENDAR,
            fx_rate=fixed_fx,
            stock_pool=self.reader.stock_pool,
        )
        self.assertEqual(first, replayed.readable())


_TRUNCATE_TABLES = (
    "securities",
    "daily_quotes",
    "dividends",
    "financials",
    "fundamentals",
    "corporate_actions",
    "action_terms",
    "ingestion_runs",
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
    """Empty the research tables so the load starts from a clean slate."""
    with engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(text(f"TRUNCATE {', '.join(_TRUNCATE_TABLES)} RESTART IDENTITY CASCADE"))


@unittest.skipUnless(_TEST_DATABASE_URL, "HARBOR_TEST_DATABASE_URL is not set")
class StorageBackedResearchSmokeTests(unittest.TestCase):
    """SP 2.14: load MockProvider data into PostgreSQL, read back, precheck."""

    def test_storage_load_with_fixed_fx_passes_precheck(self) -> None:
        engine = create_engine(_TEST_DATABASE_URL)
        _ensure_migrated()
        _reset_database(engine)

        provider = MockProvider()
        with engine.begin() as connection:
            repository = Repository(connection)
            run_id = uuid.uuid4().hex
            for market in (MarketTarget.HK, MarketTarget.US):
                repository.create_ingestion_run(
                    market.value, run_id, "mock", datetime.now(timezone.utc)
                )
                SecuritiesIngestor(repository, run_id=run_id).ingest(provider, market)
                for row in provider.list_securities(market):
                    symbol = str(row["symbol"])
                    DailyQuoteIngestor(repository, run_id=run_id).ingest(
                        provider, market, symbol, _SMOKE_START, _SMOKE_END
                    )
                    DividendIngestor(repository, run_id=run_id).ingest(
                        provider, market, symbol, _SMOKE_START, _SMOKE_END
                    )
                    FinancialIngestor(repository, run_id=run_id).ingest(provider, market, symbol)
                    CorporateActionIngestor(repository, run_id=run_id).ingest(
                        provider, market, symbol, _SMOKE_START, _SMOKE_END
                    )

        with engine.begin() as connection:
            FxRepository(connection).upsert_fx_rates(
                [
                    {
                        "from_currency": "HKD",
                        "to_currency": "USD",
                        "date": _SMOKE_START,
                        "rate": _FIXED_HKD_TO_USD,
                        "source": "mock-fixed",
                        "quality": "official",
                    }
                ]
            )

        with engine.connect() as connection:
            reader = StorageBacktestDataReader(connection)
            report = run_precheck(
                _smoke_config(),
                reader,
                _WEEKDAY_CALENDAR,
                fx_rate=reader.fx_rate,
                stock_pool=lambda market, as_of: reader.stock_pool(
                    market, as_of, historical_known=True
                ),
            )
        self.assertFalse(report.has_errors)
