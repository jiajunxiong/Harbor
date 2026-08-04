"""Ingestion tests."""

import unittest
from collections.abc import Mapping, Sequence
from typing import Any

from harbor.config import MarketTarget
from harbor.core.ingestion import SecuritiesIngestor
from harbor.infrastructure.data_providers.mock import MockProvider


class RecordingRepository:
    """A lightweight in-memory stand-in for the storage repository."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Mapping[str, Any]]]] = []

    def upsert_securities(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        self.calls.append((market, list(rows)))
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
