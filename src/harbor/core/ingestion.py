"""Ingestion orchestration for Harbor."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from harbor.config import MarketTarget
from harbor.core.interfaces import MarketDataProvider
from harbor.storage.repositories import Repository


def _store_raw_payload(
    repository: Repository,
    run_id: str | None,
    market: MarketTarget,
    endpoint: str,
    rows: Sequence[Mapping[str, Any]],
    symbol: str | None = None,
) -> None:
    """Record fetched rows as a raw payload when a run id is provided."""
    if run_id is None:
        return
    repository.record_raw_payload(
        market.value,
        run_id,
        endpoint,
        {"rows": [dict(row) for row in rows]},
        datetime.now(timezone.utc),
        symbol=symbol,
    )


class SecuritiesIngestor:
    """Fetches and stores the securities universe for a market."""

    def __init__(self, repository: Repository, *, run_id: str | None = None) -> None:
        self._repository = repository
        self._run_id = run_id

    def ingest(self, provider: MarketDataProvider, market: MarketTarget) -> int:
        """Fetch securities from the provider and upsert them.

        Args:
            provider: The data source used to list securities.
            market: The market whose universe should be ingested.

        Returns:
            The number of securities upserted.
        """
        rows = provider.list_securities(market)
        _store_raw_payload(self._repository, self._run_id, market, "securities", rows)
        return self._repository.upsert_securities(market.value, rows)


class DailyQuoteIngestor:
    """Fetches and stores daily quotes for a symbol with idempotent writes."""

    def __init__(
        self,
        repository: Repository,
        batch_size: int = 1000,
        *,
        run_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._batch_size = batch_size
        self._run_id = run_id

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
        _store_raw_payload(
            self._repository, self._run_id, market, "daily_quotes", rows, symbol=symbol
        )
        total = 0
        for batch_start in range(0, len(rows), self._batch_size):
            batch = rows[batch_start : batch_start + self._batch_size]
            total += self._repository.upsert_daily_quotes(market.value, batch)
        return total


class DividendIngestor:
    """Fetches and stores dividends for a symbol with idempotent writes."""

    def __init__(self, repository: Repository, *, run_id: str | None = None) -> None:
        self._repository = repository
        self._run_id = run_id

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
        _store_raw_payload(self._repository, self._run_id, market, "dividends", rows, symbol=symbol)
        return self._repository.upsert_dividends(market.value, rows)


class FinancialIngestor:
    """Fetches and stores financial metrics for a symbol with idempotent writes."""

    def __init__(self, repository: Repository, *, run_id: str | None = None) -> None:
        self._repository = repository
        self._run_id = run_id

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
        _store_raw_payload(
            self._repository, self._run_id, market, "financials", rows, symbol=symbol
        )
        return self._repository.upsert_financials(market.value, rows)


class CorporateActionIngestor:
    """Fetches and stores corporate actions for a symbol with idempotent writes."""

    def __init__(self, repository: Repository, *, run_id: str | None = None) -> None:
        self._repository = repository
        self._run_id = run_id

    def ingest(
        self,
        provider: MarketDataProvider,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> int:
        """Fetch corporate actions from the provider and upsert them.

        Args:
            provider: The data source used to fetch corporate actions.
            market: The market the symbol belongs to.
            symbol: The security symbol.
            start: The first relevant date to fetch.
            end: The last relevant date to fetch.

        Returns:
            The number of corporate actions upserted.
        """
        rows = provider.fetch_corporate_actions(market, symbol, start, end)
        _store_raw_payload(
            self._repository, self._run_id, market, "corporate_actions", rows, symbol=symbol
        )
        return self._repository.upsert_corporate_actions(market.value, rows)
