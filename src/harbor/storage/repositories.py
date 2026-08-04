"""Repository layer for Harbor market data.

The repository exposes market-scoped CRUD operations over the tables defined
in :mod:`harbor.storage.models`. Every public method accepts a ``market``
argument and refuses rows that target a different market, keeping Hong Kong
and United States data strictly separated.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import Connection, Insert, Select, Table, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from harbor.storage.models import (
    ActionTerm,
    AdjustedFactor,
    Base,
    CorporateAction,
    DailyQuote,
    Dividend,
    EquityEvent,
    Financial,
    Fundamental,
    IngestionRun,
    Position,
    QualityIssue,
    RawPayload,
    Security,
)


class Repository:
    """Market-scoped CRUD operations over the Harbor data model."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def _require_market(self, market: str, rows: Sequence[Mapping[str, Any]]) -> None:
        """Reject any row that does not target the requested market."""
        if any(row.get("market") != market for row in rows):
            raise ValueError(f"All rows must target market {market!r}.")

    def _table(self, model: type[Base]) -> Table:
        """Return the mapped table for a model, narrowing the declared type."""
        return cast(Table, model.__table__)

    def _upsert_statement(
        self,
        model: type[Base],
        rows: Sequence[Mapping[str, Any]],
    ) -> Insert | None:
        """Build an idempotent PostgreSQL upsert keyed on the table's primary key."""
        if not rows:
            return None
        table = self._table(model)
        primary_key = tuple(column.name for column in table.primary_key.columns)
        return (
            pg_insert(table)
            .values([dict(row) for row in rows])
            .on_conflict_do_nothing(index_elements=list(primary_key))
        )

    def _upsert(self, model: type[Base], rows: Sequence[Mapping[str, Any]]) -> int:
        """Execute an idempotent upsert and return the number of inserted rows.

        ``ON CONFLICT DO NOTHING`` does not report a reliable row count, so the
        primary keys of the actually-inserted rows are captured via ``RETURNING``
        and counted.
        """
        statement = self._upsert_statement(model, rows)
        if statement is None:
            return 0
        table = self._table(model)
        result = self._connection.execute(statement.returning(*table.primary_key.columns))
        return len(result.fetchall())

    def _insert_statement(
        self,
        model: type[Base],
        rows: Sequence[Mapping[str, Any]],
    ) -> Insert | None:
        """Build an append-only insert statement for audit log tables."""
        if not rows:
            return None
        return self._table(model).insert().values([dict(row) for row in rows])

    def _insert(self, model: type[Base], rows: Sequence[Mapping[str, Any]]) -> int:
        """Execute an append-only insert and return the number of inserted rows."""
        statement = self._insert_statement(model, rows)
        if statement is None:
            return 0
        result = self._connection.execute(statement)
        return result.rowcount or 0

    def upsert_securities(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert securities for a single market."""
        self._require_market(market, rows)
        return self._upsert(Security, rows)

    def upsert_daily_quotes(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert daily quotes for a single market."""
        self._require_market(market, rows)
        return self._upsert(DailyQuote, rows)

    def upsert_dividends(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert dividends for a single market."""
        self._require_market(market, rows)
        return self._upsert(Dividend, rows)

    def upsert_financials(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert financial indicators for a single market."""
        self._require_market(market, rows)
        return self._upsert(Financial, rows)

    def upsert_fundamentals(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert fundamentals for a single market."""
        self._require_market(market, rows)
        return self._upsert(Fundamental, rows)

    def upsert_corporate_actions(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert corporate actions for a single market."""
        self._require_market(market, rows)
        return self._upsert(CorporateAction, rows)

    def upsert_action_terms(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert action terms for a single market."""
        self._require_market(market, rows)
        return self._upsert(ActionTerm, rows)

    def upsert_positions(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert position snapshots for a single market."""
        self._require_market(market, rows)
        return self._upsert(Position, rows)

    def upsert_equity_events(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert equity events for a single market."""
        self._require_market(market, rows)
        return self._upsert(EquityEvent, rows)

    def upsert_adjusted_factors(self, market: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently upsert adjusted factors for a single market."""
        self._require_market(market, rows)
        return self._upsert(AdjustedFactor, rows)

    def create_ingestion_run(
        self,
        market: str,
        run_id: str,
        source: str,
        start_time: datetime,
    ) -> int:
        """Insert a new ingestion run for a market, idempotent on the run id."""
        return self._upsert(
            IngestionRun,
            [
                {
                    "run_id": run_id,
                    "start_time": start_time,
                    "status": "running",
                    "market": market,
                    "source": source,
                    "records_processed": 0,
                }
            ],
        )

    def complete_ingestion_run(
        self,
        market: str,
        run_id: str,
        status: str,
        records_processed: int,
        end_time: datetime,
        errors: str | None = None,
    ) -> int:
        """Update an ingestion run with its completion details."""
        statement = (
            update(IngestionRun)
            .where(IngestionRun.run_id == run_id, IngestionRun.market == market)
            .values(
                status=status,
                records_processed=records_processed,
                end_time=end_time,
                errors=errors,
            )
        )
        result = self._connection.execute(statement)
        return result.rowcount or 0

    def record_raw_payload(
        self,
        market: str,
        run_id: str,
        endpoint: str,
        payload: dict[str, object],
        retrieved_at: datetime,
        symbol: str | None = None,
    ) -> int:
        """Store a raw provider response captured during an ingestion run."""
        return self._insert(
            RawPayload,
            [
                {
                    "run_id": run_id,
                    "market": market,
                    "symbol": symbol,
                    "endpoint": endpoint,
                    "payload": payload,
                    "retrieved_at": retrieved_at,
                }
            ],
        )

    def record_quality_issue(
        self,
        market: str,
        run_id: str,
        check_name: str,
        severity: str,
        symbol: str | None = None,
        details: str | None = None,
    ) -> int:
        """Record a data-quality finding for an ingestion run."""
        return self._insert(
            QualityIssue,
            [
                {
                    "run_id": run_id,
                    "market": market,
                    "symbol": symbol,
                    "check_name": check_name,
                    "severity": severity,
                    "details": details,
                    "resolved": False,
                }
            ],
        )

    def list_securities(self, market: str) -> Select[Any]:
        """Return a market-scoped securities query."""
        return select(Security).where(Security.market == market)

    def list_daily_quotes(
        self,
        market: str,
        symbol: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> Select[Any]:
        """Return a market-scoped daily quotes query with optional filters."""
        statement = select(DailyQuote).where(DailyQuote.market == market)
        if symbol is not None:
            statement = statement.where(DailyQuote.symbol == symbol)
        if start is not None:
            statement = statement.where(DailyQuote.date >= start)
        if end is not None:
            statement = statement.where(DailyQuote.date <= end)
        return statement

    def _quality_issues_statement(
        self,
        market: str,
        run_id: str | None = None,
    ) -> Select[Any]:
        """Build a market-scoped quality issues query, optionally for a run."""
        statement = select(QualityIssue).where(QualityIssue.market == market)
        if run_id is not None:
            statement = statement.where(QualityIssue.run_id == run_id)
        return statement

    def fetch_quality_issues(
        self,
        market: str,
        run_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Return quality issues for a market, optionally filtered by run."""
        statement = self._quality_issues_statement(market, run_id)
        result = self._connection.execute(statement)
        return [dict(row) for row in result.mappings()]

    def list_corporate_actions(
        self,
        market: str,
        symbol: str | None = None,
    ) -> Select[Any]:
        """Return a market-scoped corporate actions query with a symbol filter."""
        statement = select(CorporateAction).where(CorporateAction.market == market)
        if symbol is not None:
            statement = statement.where(CorporateAction.symbol == symbol)
        return statement

    def list_dividends(
        self,
        market: str,
        symbol: str | None = None,
    ) -> Select[Any]:
        """Return a market-scoped dividends query with a symbol filter."""
        statement = select(Dividend).where(Dividend.market == market)
        if symbol is not None:
            statement = statement.where(Dividend.symbol == symbol)
        return statement

    def list_financials(
        self,
        market: str,
        symbol: str | None = None,
    ) -> Select[Any]:
        """Return a market-scoped financials query with a symbol filter."""
        statement = select(Financial).where(Financial.market == market)
        if symbol is not None:
            statement = statement.where(Financial.symbol == symbol)
        return statement

    def list_positions(
        self,
        market: str,
        symbol: str | None = None,
    ) -> Select[Any]:
        """Return a market-scoped positions query with a symbol filter."""
        statement = select(Position).where(Position.market == market)
        if symbol is not None:
            statement = statement.where(Position.symbol == symbol)
        return statement
