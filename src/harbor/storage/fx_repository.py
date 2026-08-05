"""Repository for daily FX rates (MVP 2 / SP 2.12).

FX rates are scoped by currency pair and date rather than by market, so this
repository is separate from the market-scoped :class:`Repository`.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, cast

from sqlalchemy import Connection, Insert, Select, Table, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from harbor.storage.models import Base, FxRate


class FxRepository:
    """CRUD for the ``fx_rates`` table."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    @staticmethod
    def _table(model: type[Base]) -> Table:
        """Return the mapped table for a model, narrowing the declared type."""
        return cast(Table, model.__table__)

    def _upsert_statement(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> Insert | None:
        """Build an idempotent upsert keyed on the FX rate primary key."""
        if not rows:
            return None
        table = self._table(FxRate)
        primary_key = tuple(column.name for column in table.primary_key.columns)
        return (
            pg_insert(table)
            .values([dict(row) for row in rows])
            .on_conflict_do_nothing(index_elements=list(primary_key))
        )

    def upsert_fx_rates(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently insert daily FX rates and return the inserted count."""
        statement = self._upsert_statement(rows)
        if statement is None:
            return 0
        result = self._connection.execute(statement.returning(FxRate.from_currency))
        return len(result.fetchall())

    def list_fx_rates(
        self,
        from_currency: str,
        to_currency: str,
        start: date | None = None,
        end: date | None = None,
    ) -> Select[Any]:
        """Return an FX-rate query for a pair, optionally within a date range."""
        statement = select(FxRate).where(
            FxRate.from_currency == from_currency,
            FxRate.to_currency == to_currency,
        )
        if start is not None:
            statement = statement.where(FxRate.date >= start)
        if end is not None:
            statement = statement.where(FxRate.date <= end)
        return statement.order_by(FxRate.date)
