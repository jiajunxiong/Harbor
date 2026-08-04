"""Ingestion tests."""

import unittest
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from harbor.config import MarketTarget
from harbor.core.ingestion import (
    CorporateActionIngestor,
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
        self.corporate_action_calls: list[tuple[str, list[Mapping[str, Any]]]] = []
        self.raw_payload_calls: list[dict[str, object]] = []
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

    def upsert_corporate_actions(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.corporate_action_calls.append((market, list(rows)))
        return len(rows)

    def record_raw_payload(
        self,
        market: str,
        run_id: str,
        endpoint: str,
        payload: Mapping[str, object],
        retrieved_at: datetime,
        symbol: str | None = None,
    ) -> int:
        self.raw_payload_calls.append(
            {
                "market": market,
                "run_id": run_id,
                "endpoint": endpoint,
                "payload": dict(payload),
                "retrieved_at": retrieved_at,
                "symbol": symbol,
            }
        )
        return 1


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


class CorporateActionIngestionTests(unittest.TestCase):
    """Verify the corporate actions ingestion orchestration."""

    def test_ingest_hk_corporate_actions_upserts_provider_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = CorporateActionIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(
            MockProvider(), MarketTarget.HK, "0005.HK", date(2015, 1, 1), date(2026, 12, 31)
        )

        self.assertGreater(count, 0)
        market, rows = repository.corporate_action_calls[0]
        self.assertEqual(market, "HK")
        self.assertEqual(len(rows), count)
        hk_types = {"rights_issue", "consolidation", "tender_offer", "dividend"}
        for row in rows:
            self.assertEqual(row["market"], "HK")
            self.assertEqual(row["symbol"], "0005.HK")
            self.assertIn(row["action_type"], hk_types)

    def test_ingest_us_corporate_actions_uses_us_rows(self) -> None:
        repository = RecordingRepository()
        ingestor = CorporateActionIngestor(repository)  # type: ignore[arg-type]

        count = ingestor.ingest(
            MockProvider(), MarketTarget.US, "AAPL", date(2015, 1, 1), date(2026, 12, 31)
        )

        self.assertGreater(count, 0)
        market, rows = repository.corporate_action_calls[0]
        self.assertEqual(market, "US")
        us_types = {"split", "merger", "spin_off", "dividend"}
        for row in rows:
            self.assertEqual(row["symbol"], "AAPL")
            self.assertIn(row["action_type"], us_types)


class RawPayloadRecordingTests(unittest.TestCase):
    """Verify raw payload storage during ingestion."""

    def test_ingest_daily_quotes_records_raw_payload_with_market(self) -> None:
        repository = RecordingRepository()
        ingestor = DailyQuoteIngestor(repository, run_id="run-1")  # type: ignore[arg-type]

        count = ingestor.ingest(
            MockProvider(), MarketTarget.HK, "0700.HK", date(2026, 1, 5), date(2026, 1, 9)
        )

        self.assertGreater(count, 0)
        self.assertEqual(len(repository.raw_payload_calls), 1)
        call = repository.raw_payload_calls[0]
        self.assertEqual(call["market"], "HK")
        self.assertEqual(call["run_id"], "run-1")
        self.assertEqual(call["endpoint"], "daily_quotes")
        self.assertEqual(call["symbol"], "0700.HK")
        payload_rows = call["payload"]["rows"]  # type: ignore[index]
        self.assertEqual(len(payload_rows), count)

    def test_ingest_without_run_id_records_no_payload(self) -> None:
        repository = RecordingRepository()
        ingestor = DailyQuoteIngestor(repository)  # type: ignore[arg-type]

        ingestor.ingest(
            MockProvider(), MarketTarget.HK, "0700.HK", date(2026, 1, 5), date(2026, 1, 9)
        )

        self.assertEqual(repository.raw_payload_calls, [])

    def test_ingest_financials_records_payload_tagged_with_market(self) -> None:
        repository = RecordingRepository()
        ingestor = FinancialIngestor(repository, run_id="run-2")  # type: ignore[arg-type]

        ingestor.ingest(MockProvider(), MarketTarget.US, "AAPL")

        self.assertEqual(len(repository.raw_payload_calls), 1)
        call = repository.raw_payload_calls[0]
        self.assertEqual(call["market"], "US")
        self.assertEqual(call["endpoint"], "financials")
        self.assertEqual(call["symbol"], "AAPL")
