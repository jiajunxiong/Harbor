"""Repository for backtest run master records (MVP 2 / SP 2.6).

Backtest runs may span multiple markets (HK, US or cross-market), so these
operations are keyed by ``run_id`` rather than by a single ``market``. The
status vocabulary mirrors :class:`harbor.core.backtest_domain.BacktestStatus`
(SP 2.46), keeping the persisted state machine in sync with the domain.

``config_snapshot`` must be JSON-serializable (for example the result of
``BacktestConfig.model_dump(mode="json")``); the orchestration layer (SP 2.47)
is responsible for deriving it from a validated configuration.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import Connection, Insert, Select, Table, Update, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from harbor.core.backtest_domain import BacktestStatus
from harbor.storage.models import (
    BacktestFill,
    BacktestMetric,
    BacktestNetValue,
    BacktestPosition,
    BacktestRebalance,
    BacktestRejectedTrade,
    BacktestRun,
    Base,
)

_BACKTEST_STATUSES = frozenset(status.value for status in BacktestStatus)


class BacktestRepository:
    """CRUD for the ``backtest_runs`` master table."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    @staticmethod
    def _validate_status(status: str) -> str:
        """Return a status if it is part of the domain state machine, else raise."""
        if status not in _BACKTEST_STATUSES:
            raise ValueError(
                f"Unknown backtest status {status!r}; expected one of {sorted(_BACKTEST_STATUSES)}."
            )
        return status

    def _create_statement(
        self,
        *,
        run_id: str,
        config_hash: str,
        config_snapshot: Mapping[str, Any],
        strategy: str,
        strategy_version: str,
        code_version: str,
        data_cutoff: date,
        started_at: datetime,
        status: str,
    ) -> Insert:
        """Build an idempotent insert keyed on ``run_id`` (SP 2.48)."""
        return (
            pg_insert(BacktestRun)
            .values(
                {
                    "run_id": run_id,
                    "config_hash": config_hash,
                    "config_snapshot": dict(config_snapshot),
                    "strategy": strategy,
                    "strategy_version": strategy_version,
                    "code_version": code_version,
                    "data_cutoff": data_cutoff,
                    "status": self._validate_status(status),
                    "started_at": started_at,
                }
            )
            .on_conflict_do_nothing(index_elements=["run_id"])
        )

    def _update_statement(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: datetime | None,
        error_summary: str | None,
    ) -> Update:
        """Build an update for a run's lifecycle status and diagnostics."""
        return (
            update(BacktestRun)
            .where(BacktestRun.run_id == run_id)
            .values(
                status=self._validate_status(status),
                finished_at=finished_at,
                error_summary=error_summary,
            )
        )

    def create_run(
        self,
        *,
        run_id: str,
        config_hash: str,
        config_snapshot: Mapping[str, Any],
        strategy: str,
        strategy_version: str,
        code_version: str,
        data_cutoff: date,
        started_at: datetime,
        status: str = BacktestStatus.RUNNING.value,
    ) -> int:
        """Insert a run master record, idempotent on ``run_id``.

        An existing run with the same id is left untouched: a re-run must not
        silently overwrite prior results (SP 2.48). Returns the number of rows
        actually inserted.
        """
        statement = self._create_statement(
            run_id=run_id,
            config_hash=config_hash,
            config_snapshot=config_snapshot,
            strategy=strategy,
            strategy_version=strategy_version,
            code_version=code_version,
            data_cutoff=data_cutoff,
            started_at=started_at,
            status=status,
        )
        result = self._connection.execute(statement.returning(BacktestRun.run_id))
        return len(result.fetchall())

    def update_run(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: datetime | None = None,
        error_summary: str | None = None,
    ) -> int:
        """Update a run's lifecycle status and optional diagnostics.

        Returns the number of rows updated (0 if the run does not exist).
        """
        statement = self._update_statement(
            run_id=run_id,
            status=status,
            finished_at=finished_at,
            error_summary=error_summary,
        )
        result = self._connection.execute(statement)
        return result.rowcount or 0

    def get_run(self, run_id: str) -> Select[Any]:
        """Return a query for a single run by id (SP 2.66 audit lookup)."""
        return select(BacktestRun).where(BacktestRun.run_id == run_id)

    def _require_market(self, market: str, rows: Sequence[Mapping[str, Any]]) -> None:
        """Reject any result row that does not target the requested market."""
        if any(row.get("market") != market for row in rows):
            raise ValueError(f"All rows must target market {market!r}.")

    def _insert_results_statement(
        self,
        model: type[Base],
        run_id: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> Insert | None:
        """Build an append-only insert of result rows tagged with the run id.

        Result rows are associated with ``run_id`` by injecting the value, so
        every artifact is traceable back to its research run (SP 2.7).
        """
        if not rows:
            return None
        table = cast(Table, model.__table__)
        values = [dict(row, backtest_run_id=run_id) for row in rows]
        return table.insert().values(values)

    def _insert_results(
        self,
        model: type[Base],
        run_id: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> int:
        """Execute an append-only insert of result rows and return the row count."""
        statement = self._insert_results_statement(model, run_id, rows)
        if statement is None:
            return 0
        result = self._connection.execute(statement)
        return result.rowcount or 0

    def insert_net_values(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Record daily net-value snapshots for a run."""
        return self._insert_results(BacktestNetValue, run_id, rows)

    def insert_positions(self, market: str, run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Record daily position snapshots for a run and market."""
        self._require_market(market, rows)
        return self._insert_results(BacktestPosition, run_id, rows)

    def insert_fills(self, market: str, run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Record executed orders (成交) for a run and market."""
        self._require_market(market, rows)
        return self._insert_results(BacktestFill, run_id, rows)

    def insert_rebalances(self, market: str, run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Record rebalance events for a run and market."""
        self._require_market(market, rows)
        return self._insert_results(BacktestRebalance, run_id, rows)

    def insert_metrics(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Record performance metrics for a run."""
        return self._insert_results(BacktestMetric, run_id, rows)

    def insert_rejected_trades(
        self, market: str, run_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        """Record refused trades with their reasons for a run and market."""
        self._require_market(market, rows)
        return self._insert_results(BacktestRejectedTrade, run_id, rows)

    def list_net_values(self, run_id: str) -> Select[Any]:
        """Return a query for a run's net-value snapshots."""
        return select(BacktestNetValue).where(BacktestNetValue.backtest_run_id == run_id)

    def list_positions(self, market: str, run_id: str) -> Select[Any]:
        """Return a market- and run-scoped position snapshots query."""
        return select(BacktestPosition).where(
            BacktestPosition.backtest_run_id == run_id,
            BacktestPosition.market == market,
        )

    def list_fills(self, market: str, run_id: str) -> Select[Any]:
        """Return a market- and run-scoped fills query."""
        return select(BacktestFill).where(
            BacktestFill.backtest_run_id == run_id,
            BacktestFill.market == market,
        )

    def list_rebalances(self, market: str, run_id: str) -> Select[Any]:
        """Return a market- and run-scoped rebalances query."""
        return select(BacktestRebalance).where(
            BacktestRebalance.backtest_run_id == run_id,
            BacktestRebalance.market == market,
        )

    def list_metrics(self, run_id: str) -> Select[Any]:
        """Return a query for a run's performance metrics."""
        return select(BacktestMetric).where(BacktestMetric.backtest_run_id == run_id)

    def list_rejected_trades(self, market: str, run_id: str) -> Select[Any]:
        """Return a market- and run-scoped rejected trades query."""
        return select(BacktestRejectedTrade).where(
            BacktestRejectedTrade.backtest_run_id == run_id,
            BacktestRejectedTrade.market == market,
        )
