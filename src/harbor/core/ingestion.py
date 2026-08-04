"""Ingestion orchestration for Harbor."""

from harbor.config import MarketTarget
from harbor.core.interfaces import MarketDataProvider
from harbor.storage.repositories import Repository


class SecuritiesIngestor:
    """Fetches and stores the securities universe for a market."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def ingest(self, provider: MarketDataProvider, market: MarketTarget) -> int:
        """Fetch securities from the provider and upsert them.

        Args:
            provider: The data source used to list securities.
            market: The market whose universe should be ingested.

        Returns:
            The number of securities upserted.
        """
        rows = provider.list_securities(market)
        return self._repository.upsert_securities(market.value, rows)
