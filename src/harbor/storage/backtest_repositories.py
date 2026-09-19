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

from sqlalchemy import Connection, Insert, Select, Table, Update, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from harbor.core.backtest_domain import BacktestStatus
from harbor.core.factor_snapshot import FactorSnapshot
from harbor.storage.models import (
    BacktestFactorSnapshot,
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

#: Sortable run fields (MVP 5 / SP 5.13). A client-supplied sort key is mapped
#: through this allow-list, so no arbitrary column or expression can reach the
#: query. Exposed so the read API can advertise the accepted values.
_RUN_SORT_COLUMNS: dict[str, Any] = {
    "run_id": BacktestRun.run_id,
    "status": BacktestRun.status,
    "strategy": BacktestRun.strategy,
    "strategy_version": BacktestRun.strategy_version,
    "data_cutoff": BacktestRun.data_cutoff,
    "started_at": BacktestRun.started_at,
    "finished_at": BacktestRun.finished_at,
}

RUN_SORT_FIELDS: tuple[str, ...] = tuple(sorted(_RUN_SORT_COLUMNS))

RUN_SORT_ORDERS: tuple[str, ...] = ("asc", "desc")


class BacktestRepository:
    """CRUD for the ``backtest_runs`` master table."""

    def __init__(self, connection: Connection, batch_size: int = 1000) -> None:
        self._connection = connection
        self._batch_size = batch_size

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
        resume_of: str | None = None,
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
                    "resume_of": resume_of,
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
        resume_of: str | None = None,
    ) -> int:
        """Insert a run master record, idempotent on ``run_id``.

        An existing run with the same id is left untouched: a re-run must not
        silently overwrite prior results (SP 2.48). ``resume_of`` links a
        resumed run back to the original run it was created from (SP 2.70).
        Returns the number of rows actually inserted.
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
            resume_of=resume_of,
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

    @staticmethod
    def _run_criteria(
        *,
        status: str | None = None,
        strategy: str | None = None,
        data_cutoff_from: date | None = None,
        data_cutoff_to: date | None = None,
    ) -> tuple[Any, ...]:
        """Build the shared WHERE criteria for the run list (MVP 5 / SP 5.13).

        The same criteria feed :meth:`list_runs` and :meth:`count_runs`, so the
        reported total always describes the filtered set rather than the whole
        table — a total that ignored the filter would silently overstate how
        many runs a screen is showing.
        """
        criteria: list[Any] = []
        if status is not None:
            criteria.append(BacktestRun.status == status)
        if strategy is not None:
            criteria.append(BacktestRun.strategy == strategy)
        if data_cutoff_from is not None:
            criteria.append(BacktestRun.data_cutoff >= data_cutoff_from)
        if data_cutoff_to is not None:
            criteria.append(BacktestRun.data_cutoff <= data_cutoff_to)
        return tuple(criteria)

    def list_runs(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        status: str | None = None,
        strategy: str | None = None,
        data_cutoff_from: date | None = None,
        data_cutoff_to: date | None = None,
        sort: str = "started_at",
        order: str = "desc",
    ) -> Select[Any]:
        """Return a query for runs, newest first by default (SP 5.8, SP 5.13).

        ``limit`` is optional so the read API can enforce its own bound while
        CLI callers ask for everything. Ordering always ends with ``run_id``
        following the same direction, so it is a *total* order: a page boundary
        can never skip or repeat a run when several share a sort value.

        Raises:
            ValueError: If ``sort`` is not allow-listed or ``order`` is unknown.
        """
        column = _RUN_SORT_COLUMNS.get(sort)
        if column is None:
            raise ValueError(
                f"Unsupported run sort field {sort!r}; choose one of {list(RUN_SORT_FIELDS)}."
            )
        if order not in RUN_SORT_ORDERS:
            raise ValueError(
                f"Unsupported run sort order {order!r}; expected one of {list(RUN_SORT_ORDERS)}."
            )
        descending = order == "desc"
        statement = (
            select(BacktestRun)
            .where(
                *self._run_criteria(
                    status=status,
                    strategy=strategy,
                    data_cutoff_from=data_cutoff_from,
                    data_cutoff_to=data_cutoff_to,
                )
            )
            .order_by(
                column.desc() if descending else column.asc(),
                BacktestRun.run_id.desc() if descending else BacktestRun.run_id.asc(),
            )
        )
        if limit is not None:
            statement = statement.limit(limit).offset(offset)
        return statement

    def count_runs(
        self,
        *,
        status: str | None = None,
        strategy: str | None = None,
        data_cutoff_from: date | None = None,
        data_cutoff_to: date | None = None,
    ) -> Select[Any]:
        """Return a query for the number of runs matching the same filters (SP 5.13)."""
        return (
            select(func.count())
            .select_from(BacktestRun)
            .where(
                *self._run_criteria(
                    status=status,
                    strategy=strategy,
                    data_cutoff_from=data_cutoff_from,
                    data_cutoff_to=data_cutoff_to,
                )
            )
        )

    def distinct_run_statuses(self) -> Select[Any]:
        """Return a query for the run statuses actually present (SP 5.13).

        A filter menu built from the *stored* values cannot offer a filter that
        matches nothing, which a hardcoded vocabulary would.
        """
        return select(BacktestRun.status).distinct().order_by(BacktestRun.status.asc())

    def distinct_run_strategies(self) -> Select[Any]:
        """Return a query for the strategy names actually present (SP 5.13)."""
        return select(BacktestRun.strategy).distinct().order_by(BacktestRun.strategy.asc())

    @staticmethod
    def _shared_input_criteria(
        *,
        config_hash: str,
        code_version: str,
        data_cutoff: date,
        exclude_run_id: str | None = None,
    ) -> tuple[Any, ...]:
        """Build the WHERE criteria for runs claiming the same replay inputs (SP 5.21).

        The replay manifest fingerprint (SP 2.61) is derived rather than stored,
        so siblings are found through the recorded inputs it is built from. The
        config hash already covers the strategy, markets and date range, so this
        trio identifies "the same experiment" as far as the database can tell.
        """
        criteria: list[Any] = [
            BacktestRun.config_hash == config_hash,
            BacktestRun.code_version == code_version,
            BacktestRun.data_cutoff == data_cutoff,
        ]
        if exclude_run_id is not None:
            criteria.append(BacktestRun.run_id != exclude_run_id)
        return tuple(criteria)

    def list_runs_sharing_inputs(
        self,
        *,
        config_hash: str,
        code_version: str,
        data_cutoff: date,
        exclude_run_id: str | None = None,
        limit: int | None = None,
    ) -> Select[Any]:
        """Return a query for runs that claim the same replay inputs (SP 5.21).

        Ordering is a total order (newest first, then ``run_id``) so a bounded
        read is deterministic rather than depending on physical row order.
        """
        statement = (
            select(BacktestRun)
            .where(
                *self._shared_input_criteria(
                    config_hash=config_hash,
                    code_version=code_version,
                    data_cutoff=data_cutoff,
                    exclude_run_id=exclude_run_id,
                )
            )
            .order_by(BacktestRun.started_at.desc(), BacktestRun.run_id.desc())
        )
        return statement.limit(limit) if limit is not None else statement

    def count_runs_sharing_inputs(
        self,
        *,
        config_hash: str,
        code_version: str,
        data_cutoff: date,
        exclude_run_id: str | None = None,
    ) -> Select[Any]:
        """Return a query for how many runs share the same inputs (SP 5.21).

        The count feeds the same criteria as the list, so a bounded read can say
        honestly how many siblings it left out instead of implying there are no
        more.
        """
        return (
            select(func.count())
            .select_from(BacktestRun)
            .where(
                *self._shared_input_criteria(
                    config_hash=config_hash,
                    code_version=code_version,
                    data_cutoff=data_cutoff,
                    exclude_run_id=exclude_run_id,
                )
            )
        )

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
        """Execute an append-only insert of result rows and return the row count.

        Rows are written in bounded batches so that large runs (e.g. a US
        backtest that fills tens of thousands of orders) never build a single
        statement with more bound parameters than PostgreSQL allows (65535 per
        statement). Every batch is tagged with ``run_id`` exactly like the
        single-shot path so all artifacts stay traceable to the run (SP 2.7).
        """
        if not rows:
            return 0
        table = cast(Table, model.__table__)
        total = 0
        for start in range(0, len(rows), self._batch_size):
            chunk = rows[start : start + self._batch_size]
            values = [dict(row, backtest_run_id=run_id) for row in chunk]
            statement = table.insert().values(values)
            result = self._connection.execute(statement)
            total += result.rowcount or 0
        return total

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
        """Return a query for a run's net-value snapshots, oldest first.

        The ordering is part of the contract, not a convenience: callers take
        the first and last snapshot as the run's start and end value (SP 2.68)
        and the metrics functions require an ascending series (SP 2.53). An
        unordered query happened to work because the table is append-only, which
        is not a guarantee.
        """
        return (
            select(BacktestNetValue)
            .where(BacktestNetValue.backtest_run_id == run_id)
            .order_by(
                BacktestNetValue.as_of_date.asc(),
                BacktestNetValue.currency.asc(),
            )
        )

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

    @staticmethod
    def _run_trade_criteria(
        model: type[Base],
        run_id: str,
        *,
        market: str | None,
        symbol: str | None,
    ) -> tuple[Any, ...]:
        """Build the run-scoped trade criteria shared by the list and count queries."""
        criteria: list[Any] = [model.backtest_run_id == run_id]  # type: ignore[attr-defined]
        if market is not None:
            criteria.append(model.market == market)  # type: ignore[attr-defined]
        if symbol is not None:
            criteria.append(model.symbol == symbol)  # type: ignore[attr-defined]
        return tuple(criteria)

    def list_run_fills(
        self,
        run_id: str,
        *,
        market: str | None = None,
        symbol: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Select[Any]:
        """Return a run-scoped fills query, optionally narrowed (MVP 5 / SP 5.18).

        A run may span markets, so this is keyed by ``run_id`` rather than by a
        single market: the per-market repositories cannot answer "every fill of
        this cross-market run" without merging several queries in the client.
        """
        statement = (
            select(BacktestFill)
            .where(*self._run_trade_criteria(BacktestFill, run_id, market=market, symbol=symbol))
            .order_by(
                BacktestFill.trade_date.asc(),
                BacktestFill.market.asc(),
                BacktestFill.symbol.asc(),
                BacktestFill.id.asc(),
            )
        )
        if limit is not None:
            statement = statement.limit(limit).offset(offset)
        return statement

    def count_run_fills(
        self, run_id: str, *, market: str | None = None, symbol: str | None = None
    ) -> Select[Any]:
        """Return a query for the number of fills matching the same filters (SP 5.18)."""
        return (
            select(func.count())
            .select_from(BacktestFill)
            .where(*self._run_trade_criteria(BacktestFill, run_id, market=market, symbol=symbol))
        )

    def list_run_rejected_trades(
        self,
        run_id: str,
        *,
        market: str | None = None,
        symbol: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Select[Any]:
        """Return a run-scoped rejected trades query (MVP 5 / SP 5.18)."""
        statement = (
            select(BacktestRejectedTrade)
            .where(
                *self._run_trade_criteria(
                    BacktestRejectedTrade, run_id, market=market, symbol=symbol
                )
            )
            .order_by(
                BacktestRejectedTrade.market.asc(),
                BacktestRejectedTrade.symbol.asc(),
                BacktestRejectedTrade.id.asc(),
            )
        )
        if limit is not None:
            statement = statement.limit(limit).offset(offset)
        return statement

    def count_run_rejected_trades(
        self, run_id: str, *, market: str | None = None, symbol: str | None = None
    ) -> Select[Any]:
        """Return a query for the number of rejected trades matching the filters (SP 5.18)."""
        return (
            select(func.count())
            .select_from(BacktestRejectedTrade)
            .where(
                *self._run_trade_criteria(
                    BacktestRejectedTrade, run_id, market=market, symbol=symbol
                )
            )
        )

    def rejected_reason_counts(
        self, run_id: str, *, market: str | None = None, symbol: str | None = None
    ) -> Select[Any]:
        """Return a query for the refusal-reason distribution of a run (SP 5.18).

        Aggregated in the database over **every** matching row, so the charted
        distribution describes the whole set rather than the page on screen.
        """
        statement = (
            select(BacktestRejectedTrade.reason, func.count())
            .where(
                *self._run_trade_criteria(
                    BacktestRejectedTrade, run_id, market=market, symbol=symbol
                )
            )
            .group_by(BacktestRejectedTrade.reason)
            .order_by(func.count().desc(), BacktestRejectedTrade.reason.asc())
        )
        return statement

    @staticmethod
    def _factor_snapshot_rows(
        market: str,
        snapshot: FactorSnapshot,
    ) -> list[dict[str, Any]]:
        """Convert a core snapshot's entries for ``market`` into row mappings.

        Only entries whose market matches ``market`` are included. Availability
        dates are serialized to ISO strings because the JSONB columns store
        JSON-compatible objects (SP 2.28).
        """
        rows: list[dict[str, Any]] = []
        for entry in snapshot.entries:
            if entry.market.value != market:
                continue
            rows.append(
                {
                    "market": market,
                    "symbol": entry.symbol,
                    "as_of_date": snapshot.as_of,
                    "raw_values": dict(entry.raw_values),
                    "availability_dates": {
                        name: day.isoformat() for name, day in entry.availability_dates
                    },
                    "standardized_scores": dict(entry.standardized_scores),
                    "composite_score": entry.composite_score,
                    "rank": entry.rank,
                    "selected": entry.selected,
                    "exclusion_reason": entry.exclusion_reason,
                }
            )
        return rows

    def insert_factor_snapshot(self, market: str, run_id: str, snapshot: FactorSnapshot) -> int:
        """Record a rebalance's factor snapshot for a run and market (SP 2.28).

        Persists one row per symbol considered at the rebalance (raw values,
        availability dates, standardized scores, composite score, rank,
        selection and exclusion reason), all tagged with ``run_id``.
        """
        rows = self._factor_snapshot_rows(market, snapshot)
        return self._insert_results(BacktestFactorSnapshot, run_id, rows)

    def list_factor_snapshots(self, market: str, run_id: str) -> Select[Any]:
        """Return a market- and run-scoped factor snapshot query (SP 2.28)."""
        return select(BacktestFactorSnapshot).where(
            BacktestFactorSnapshot.backtest_run_id == run_id,
            BacktestFactorSnapshot.market == market,
        )
