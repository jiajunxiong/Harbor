"""Ingestion tests."""

import unittest
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from harbor.config import MarketTarget
from harbor.core.ingestion import (
    DailyQuoteIngestor,
    DividendIngestor,
    FinancialIngestor,
    SecuritiesIngestor,
)
from harbor.core.interfaces import Capability, MarketDataProvider, ProviderCapabilities
from harbor.infrastructure.data_providers.mock import MockProvider


class RecordingRepository:
    """A lightweight in-memory stand-in for the storage repository."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Mapping[str, Any]]]] = []
        self.daily_quotes_calls: list[tuple[str, list[Mapping[str, Any]]]] = []
        self.dividend_calls: list[tuple[str, list[Mapping[str, Any]]]] = []
        self.financial_calls: list[tuple[str, list[Mapping[str, Any]]]] = []
        self.batch_sizes_seen: list[int] = []

    def upsert_securities(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.calls.append((market, list(rows)))
        return len(rows)

    def upsert_daily_quotes(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.daily_quotes_calls.append((market, list(rows)))
        self.batch_sizes_seen.append(len(rows))
        return len(rows)

    def upsert_dividends(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.dividend_calls.append((market, list(rows)))
        return len(rows)

    def upsert_financials(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.financial_calls.append((market, list(rows)))
        return len(rows)


class SecuritiesIngestionTests(unittest.TestCase):
    """Verify the securities ingestion orchestration."""

    def test_ingest_hk_securities_upserts_provider_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = SecuritiesIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(MockProvider(), MarketTarget.HK)

        self.assertGreaterEqual(count, 10)
        market, rows = repository.calls[0]
        self.assertEqual(market, "HK")
        self.assertEqual(len(rows), count)
        for row in rows:
            self.assertEqual(row["market"], "HK")
            self.assertTrue(row["symbol"].endswith(".HK"))

    def test_ingest_us_securities_uses_us_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = SecuritiesIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(MockProvider(), MarketTarget.US)

        self.assertGreaterEqual(count, 10)
        market, rows = repository.calls[0]
        self.assertEqual(market, "US")
        symbols = {row["symbol"] for row in rows}
        self.assertIn("AAPL", symbols)
        self.assertIn("MSFT", symbols)

    def test_ingest_returns_zero_when_provider_has_no_rows(self) -> None:
        class EmptyProvider(MockProvider):
            def list_securities(self, market: MarketTarget) -> Sequence[Mapping[str, Any]]:
                return []

        repository = RecordingRepository()
        ingestor = SecuritiesIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(EmptyProvider(), MarketTarget.HK)

        self.assertEqual(count, 0)
        market, rows = repository.calls[0]
        self.assertEqual(rows, [])


class DailyQuoteIngestionTests(unittest.TestCase):
    """Verify the daily quotes ingestion orchestration."""

    def test_ingest_hk_daily_quotes_upserts_provider_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = DailyQuoteIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(
            MockProvider(), MarketTarget.HK, "0700.HK", date(2026, 1, 5), date(2026, 1, 9)
        )

        self.assertGreater(count, 0)
        market, rows = repository.daily_quotes_calls[0]
        self.assertEqual(market, "HK")
        self.assertEqual(len(rows), count)
        for row in rows:
            self.assertEqual(row["market"], "HK")
            self.assertEqual(row["symbol"], "0700.HK")

    def test_ingest_us_daily_quotes_uses_us_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = DailyQuoteIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(
            MockProvider(), MarketTarget.US, "AAPL", date(2026, 1, 5), date(2026, 1, 9)
        )

        self.assertGreater(count, 0)
        market, rows = repository.daily_quotes_calls[0]
        self.assertEqual(market, "US")
        self.assertEqual(rows[0]["symbol"], "AAPL")

    def test_ingest_batches_large_result_sets(self) -> None:
        class StubProvider(MarketDataProvider):
            def capabilities(self) -> ProviderCapabilities:
                return ProviderCapabilities({MarketTarget.HK: frozenset({Capability.DAILY_QUOTES})})

            def fetch_daily_quotes(
                self,
                market: MarketTarget,
                symbol: str,
                start: date,
                end: date,
            ) -> Sequence[Mapping[str, Any]]:
                return [{"market": "HK", "symbol": symbol, "date": date(2026, 1, 1)}] * 25

        repository = RecordingRepository()
        ingestor = DailyQuoteIngestor(repository, batch_size=10)  # type: ignore[arg-type]

        count = ingestor.ingest(
            StubProvider(), MarketTarget.HK, "0700.HK", date(2026, 1, 1), date(2026, 1, 31)
        )

        self.assertEqual(count, 25)
        self.assertEqual(repository.batch_sizes_seen, [10, 10, 5])


class DividendIngestionTests(unittest.TestCase):
    """Verify the dividends ingestion orchestration."""

    def test_ingest_hk_dividends_upserts_provider_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = DividendIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(
            MockProvider(), MarketTarget.HK, "0700.HK", date(2025, 1, 1), date(2026, 12, 31)
        )

        self.assertGreater(count, 0)
        market, rows = repository.dividend_calls[0]
        self.assertEqual(market, "HK")
        self.assertEqual(len(rows), count)
        for row in rows:
            self.assertEqual(row["market"], "HK")
            self.assertEqual(row["symbol"], "0700.HK")
            self.assertEqual(row["currency"], "HKD")

    def test_ingest_us_dividends_uses_us_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = DividendIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(
            MockProvider(), MarketTarget.US, "AAPL", date(2025, 1, 1), date(2026, 12, 31)
        )

        self.assertGreater(count, 0)
        market, rows = repository.dividend_calls[0]
        self.assertEqual(market, "US")
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertEqual(rows[0]["currency"], "USD")


class FinancialIngestionTests(unittest.TestCase):
    """Verify the financials ingestion orchestration."""

    def test_ingest_hk_financials_upserts_provider_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = FinancialIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(MockProvider(), MarketTarget.HK, "0700.HK")

        self.assertGreater(count, 0)
        market, rows = repository.financial_calls[0]
        self.assertEqual(market, "HK")
        self.assertEqual(len(rows), count)
        for row in rows:
            self.assertEqual(row["market"], "HK")
            self.assertEqual(row["symbol"], "0700.HK")

    def test_ingest_us_financials_uses_us_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = FinancialIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(MockProvider(), MarketTarget.US, "AAPL")

        self.assertGreater(count, 0)
        market, rows = repository.financial_calls[0]
        self.assertEqual(market, "US")
        self.assertEqual(rows[0]["symbol"], "AAPL")
