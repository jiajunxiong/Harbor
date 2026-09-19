"""Read-only data access for the API (MVP 5 / SP 5.8).

Every read goes through the existing storage repositories — the API never
issues ad-hoc SQL over domain tables and never writes — so persistence
conventions (idempotent inserts, status vocabularies) stay in one place and
the dashboard cannot bypass domain validation.

Rows are returned as plain mappings; the router layer validates them into
response schemas. The :class:`ReadStore` protocol keeps the routers testable
without a database.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from sqlalchemy import Connection, func, select

from harbor.storage.backtest_repositories import BacktestRepository
from harbor.storage.models import (
    BacktestFill,
    BacktestMetric,
    BacktestNetValue,
    BacktestPosition,
    BacktestRejectedTrade,
    CircuitBreaker,
    DailyQuote,
    IngestionRun,
    PaperFill,
    PaperNetValue,
    PaperOrder,
    PaperReconciliationDifference,
    QualityIssue,
    RiskApproval,
    Security,
    ValidationWarning,
)
from harbor.storage.paper_repositories import PaperRepository
from harbor.storage.validation_repositories import ValidationRepository

Row = dict[str, Any]


class ReadStore(Protocol):
    """The read-only queries the API needs (MVP 5 / SP 5.8)."""

    def list_backtest_runs(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        strategy: str | None = None,
        data_cutoff_from: date | None = None,
        data_cutoff_to: date | None = None,
        sort: str = "started_at",
        order: str = "desc",
    ) -> tuple[list[Row], int]: ...

    def get_backtest_run(self, run_id: str) -> Row | None: ...

    def backtest_run_stats(self, run_id: str) -> dict[str, int]: ...

    def list_net_values(self, run_id: str) -> list[Row]: ...

    def list_fills(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
        market: str | None = None,
        symbol: str | None = None,
    ) -> tuple[list[Row], int]: ...

    def list_rejected_trades(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
        market: str | None = None,
        symbol: str | None = None,
    ) -> tuple[list[Row], int]: ...

    def rejected_reason_counts(
        self,
        run_id: str,
        *,
        market: str | None = None,
        symbol: str | None = None,
    ) -> list[tuple[str, int]]: ...

    def run_filter_options(self) -> tuple[list[str], list[str]]: ...

    def list_validation_runs(self, *, limit: int, offset: int) -> tuple[list[Row], int]: ...

    def get_validation_run(self, run_id: str) -> Row | None: ...

    def get_validation_manifest(self, run_id: str) -> Row | None: ...

    def get_validation_conclusion(self, run_id: str) -> Row | None: ...

    def validation_warning_stats(self, run_id: str) -> tuple[int, dict[str, int]]: ...

    def list_paper_runs(self, *, limit: int, offset: int) -> tuple[list[Row], int]: ...

    def get_paper_run(self, run_id: str) -> Row | None: ...

    def paper_run_stats(self, run_id: str) -> dict[str, int]: ...

    def quality_summary(self, market: str) -> Row: ...


class SqlReadStore:
    """A :class:`ReadStore` backed by the storage repositories (SP 5.8)."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    # -- run collections -------------------------------------------------

    def list_backtest_runs(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        strategy: str | None = None,
        data_cutoff_from: date | None = None,
        data_cutoff_to: date | None = None,
        sort: str = "started_at",
        order: str = "desc",
    ) -> tuple[list[Row], int]:
        """Return one page of backtest runs plus the filtered total (SP 5.8/5.13).

        The filters feed the count as well as the page, so ``total`` describes
        the filtered set the caller is looking at rather than the whole table.
        """
        repository = BacktestRepository(self._connection)
        rows = self._connection.execute(
            repository.list_runs(
                limit=limit,
                offset=offset,
                status=status,
                strategy=strategy,
                data_cutoff_from=data_cutoff_from,
                data_cutoff_to=data_cutoff_to,
                sort=sort,
                order=order,
            )
        ).all()
        total = int(
            self._connection.execute(
                repository.count_runs(
                    status=status,
                    strategy=strategy,
                    data_cutoff_from=data_cutoff_from,
                    data_cutoff_to=data_cutoff_to,
                )
            ).scalar_one()
        )
        return [dict(row._mapping) for row in rows], total

    def list_net_values(self, run_id: str) -> list[Row]:
        """Return a run's net-value snapshots, oldest first (SP 5.15)."""
        repository = BacktestRepository(self._connection)
        return [
            dict(row._mapping)
            for row in self._connection.execute(repository.list_net_values(run_id)).all()
        ]

    def list_fills(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
        market: str | None = None,
        symbol: str | None = None,
    ) -> tuple[list[Row], int]:
        """Return one page of a run's fills plus the filtered total (SP 5.18)."""
        repository = BacktestRepository(self._connection)
        rows = self._connection.execute(
            repository.list_run_fills(
                run_id, market=market, symbol=symbol, limit=limit, offset=offset
            )
        ).all()
        total = int(
            self._connection.execute(
                repository.count_run_fills(run_id, market=market, symbol=symbol)
            ).scalar_one()
        )
        return [dict(row._mapping) for row in rows], total

    def list_rejected_trades(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
        market: str | None = None,
        symbol: str | None = None,
    ) -> tuple[list[Row], int]:
        """Return one page of a run's refused trades plus the total (SP 5.18)."""
        repository = BacktestRepository(self._connection)
        rows = self._connection.execute(
            repository.list_run_rejected_trades(
                run_id, market=market, symbol=symbol, limit=limit, offset=offset
            )
        ).all()
        total = int(
            self._connection.execute(
                repository.count_run_rejected_trades(run_id, market=market, symbol=symbol)
            ).scalar_one()
        )
        return [dict(row._mapping) for row in rows], total

    def rejected_reason_counts(
        self,
        run_id: str,
        *,
        market: str | None = None,
        symbol: str | None = None,
    ) -> list[tuple[str, int]]:
        """Return the refusal reasons of a run with their full-set counts (SP 5.18)."""
        repository = BacktestRepository(self._connection)
        rows = self._connection.execute(
            repository.rejected_reason_counts(run_id, market=market, symbol=symbol)
        ).all()
        return [(str(reason), int(count)) for reason, count in rows]

    def run_filter_options(self) -> tuple[list[str], list[str]]:
        """Return the statuses and strategies present in the run table (SP 5.13)."""
        repository = BacktestRepository(self._connection)
        statuses = [
            str(value)
            for value in self._connection.execute(repository.distinct_run_statuses()).scalars()
        ]
        strategies = [
            str(value)
            for value in self._connection.execute(repository.distinct_run_strategies()).scalars()
        ]
        return statuses, strategies

    def list_validation_runs(self, *, limit: int, offset: int) -> tuple[list[Row], int]:
        """Return one page of validation runs, newest first (SP 5.8)."""
        repository = ValidationRepository(self._connection)
        rows = self._connection.execute(repository.list_runs(limit=limit, offset=offset)).all()
        total = int(self._connection.execute(repository.count_runs()).scalar_one())
        return [dict(row._mapping) for row in rows], total

    def list_paper_runs(self, *, limit: int, offset: int) -> tuple[list[Row], int]:
        """Return one page of paper runs, newest first (SP 5.8)."""
        repository = PaperRepository(self._connection)
        rows = self._connection.execute(repository.list_runs(limit=limit, offset=offset)).all()
        total = int(self._connection.execute(repository.count_runs()).scalar_one())
        return [dict(row._mapping) for row in rows], total

    # -- single runs -----------------------------------------------------

    def get_backtest_run(self, run_id: str) -> Row | None:
        """Return one backtest run, or ``None`` when it does not exist (SP 5.8)."""
        repository = BacktestRepository(self._connection)
        row = self._connection.execute(repository.get_run(run_id)).first()
        return dict(row._mapping) if row is not None else None

    def get_validation_run(self, run_id: str) -> Row | None:
        """Return one validation run, or ``None`` when it does not exist (SP 5.8)."""
        repository = ValidationRepository(self._connection)
        row = self._connection.execute(repository.get_run(run_id)).first()
        return dict(row._mapping) if row is not None else None

    def get_paper_run(self, run_id: str) -> Row | None:
        """Return one paper run, or ``None`` when it does not exist (SP 5.8)."""
        repository = PaperRepository(self._connection)
        row = self._connection.execute(repository.get_run(run_id)).first()
        return dict(row._mapping) if row is not None else None

    def get_validation_manifest(self, run_id: str) -> Row | None:
        """Return a validation run's frozen dataset manifest (SP 3.6)."""
        repository = ValidationRepository(self._connection)
        row = self._connection.execute(repository.get_manifest(run_id)).first()
        return dict(row._mapping) if row is not None else None

    def get_validation_conclusion(self, run_id: str) -> Row | None:
        """Return a validation run's recorded out-of-sample conclusion (SP 3.58)."""
        repository = ValidationRepository(self._connection)
        row = self._connection.execute(repository.get_conclusion(run_id)).first()
        return dict(row._mapping) if row is not None else None

    def validation_warning_stats(self, run_id: str) -> tuple[int, dict[str, int]]:
        """Return ``(total, per-severity)`` warning counts for a run (SP 3.12)."""
        total = self._count(ValidationWarning, ValidationWarning.validation_run_id == run_id)
        rows = self._connection.execute(
            select(ValidationWarning.severity, func.count())
            .where(ValidationWarning.validation_run_id == run_id)
            .group_by(ValidationWarning.severity)
        ).all()
        return total, {str(severity): int(count) for severity, count in rows}

    # -- run statistics --------------------------------------------------

    def backtest_run_stats(self, run_id: str) -> dict[str, int]:
        """Return artifact counts for one backtest run (SP 5.8)."""
        return {
            "net_value_points": self._count(
                BacktestNetValue, BacktestNetValue.backtest_run_id == run_id
            ),
            "positions": self._count(BacktestPosition, BacktestPosition.backtest_run_id == run_id),
            "fills": self._count(BacktestFill, BacktestFill.backtest_run_id == run_id),
            "metrics": self._count(BacktestMetric, BacktestMetric.backtest_run_id == run_id),
            "rejected_trades": self._count(
                BacktestRejectedTrade,
                BacktestRejectedTrade.backtest_run_id == run_id,
            ),
        }

    def paper_run_stats(self, run_id: str) -> dict[str, int]:
        """Return artifact counts for one paper run (SP 5.8)."""
        return {
            "orders": self._count(PaperOrder, PaperOrder.paper_run_id == run_id),
            "fills": self._count(PaperFill, PaperFill.paper_run_id == run_id),
            "net_value_points": self._count(PaperNetValue, PaperNetValue.paper_run_id == run_id),
            "approvals": self._count(RiskApproval, RiskApproval.paper_run_id == run_id),
            "circuit_breakers": self._count(CircuitBreaker, CircuitBreaker.paper_run_id == run_id),
            "reconciliation_differences": self._count(
                PaperReconciliationDifference,
                PaperReconciliationDifference.paper_run_id == run_id,
            ),
        }

    # -- data quality ----------------------------------------------------

    def quality_summary(self, market: str) -> Row:
        """Return the data-quality summary the dashboard shows for a market (SP 5.8)."""
        issue_filters = (QualityIssue.market == market, QualityIssue.resolved.is_(False))
        severity_rows = self._connection.execute(
            select(QualityIssue.severity, func.count())
            .where(*issue_filters)
            .group_by(QualityIssue.severity)
        ).all()
        latest_ingestion = self._connection.execute(
            select(IngestionRun)
            .where(IngestionRun.market.in_((market, "BOTH")))
            .order_by(IngestionRun.start_time.desc(), IngestionRun.run_id.desc())
            .limit(1)
        ).first()
        return {
            "market": market,
            "security_count": self._count(Security, Security.market == market),
            "bar_count": self._count(DailyQuote, DailyQuote.market == market),
            "latest_bar_date": self._connection.execute(
                select(func.max(DailyQuote.date)).where(DailyQuote.market == market)
            ).scalar_one(),
            "open_issue_count": self._count(QualityIssue, *issue_filters),
            "issues_by_severity": {str(severity): int(count) for severity, count in severity_rows},
            "latest_ingestion": (
                dict(latest_ingestion._mapping) if latest_ingestion is not None else None
            ),
        }

    # -- helpers ---------------------------------------------------------

    def _count(self, model: type[Any], *criteria: Any) -> int:
        """Count rows of ``model`` matching every criterion."""
        statement = select(func.count()).select_from(model)
        for criterion in criteria:
            statement = statement.where(criterion)
        return int(self._connection.execute(statement).scalar_one())
