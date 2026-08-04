"""Ingestion orchestration for Harbor."""

from datetime import date

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


class DailyQuoteIngestor:
    """Fetches and stores daily quotes for a symbol with idempotent writes."""

    def __init__(self, repository: Repository, batch_size: int = 1000) -> None:
        self._repository = repository
        self._batch_size = batch_size

    def ingest(
        self,
        provider: MarketDataProvider,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> int:
        """Fetch daily quotes from the provider and upsert them in batches.

        Rows are written in batches so that large universes, such as the
        United States market, do not build a single oversized statement. The
        repository guarantees idempotency via ``ON CONFLICT (market, symbol,
        date) DO NOTHING``.

        Args:
            provider: The data source used to fetch daily quotes.
            market: The market the symbol belongs to.
            symbol: The security symbol.
            start: The first trading date to fetch.
            end: The last trading date to fetch.

        Returns:
            The number of daily quotes upserted.
        """
        rows = provider.fetch_daily_quotes(market, symbol, start, end)
        total = 0
        for batch_start in range(0, len(rows), self._batch_size):
            batch = rows[batch_start : batch_start + self._batch_size]
            total += self._repository.upsert_daily_quotes(market.value, batch)
        return total


class DividendIngestor:
    """Fetches and stores dividends for a symbol with idempotent writes."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def ingest(
        self,
        provider: MarketDataProvider,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> int:
        """Fetch dividends from the provider and upsert them.

        Args:
            provider: The data source used to fetch dividends.
            market: The market the symbol belongs to.
            symbol: The security symbol.
            start: The first ex-date to fetch.
            end: The last ex-date to fetch.

        Returns:
            The number of dividends upserted.
        """
        rows = provider.fetch_dividends(market, symbol, start, end)
        return self._repository.upsert_dividends(market.value, rows)


class FinancialIngestor:
    """Fetches and stores financial metrics for a symbol with idempotent writes."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def ingest(
        self,
        provider: MarketDataProvider,
        market: MarketTarget,
        symbol: str,
    ) -> int:
        """Fetch financial metrics from the provider and upsert them.

        Args:
            provider: The data source used to fetch financial metrics.
            market: The market the symbol belongs to.
            symbol: The security symbol.

        Returns:
            The number of financial rows upserted.
        """
        rows = provider.fetch_financials(market, symbol)
        return self._repository.upsert_financials(market.value, rows)
