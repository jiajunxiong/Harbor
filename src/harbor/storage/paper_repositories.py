"""Repository for paper-trading runs and artifacts (MVP 4 / SP 4.5-4.8).

A paper run records its master row (config hash, dataset fingerprint, code
version, market scope, base currency and lifecycle status) plus orders, fills,
risk approvals, circuit breakers, daily net values and reconciliation
differences, all linked by ``paper_run_id`` so every paper artifact is
traceable to its run (SP 4.5-4.8).

The status vocabulary mirrors :class:`harbor.core.paper_domain.PaperStatus`
(SP 4.10), keeping the persisted state machine in sync with the domain.
``config_snapshot`` must be JSON-serializable (for example the result of
``PaperConfig.model_dump(mode="json")``); the orchestration layer is
responsible for deriving it from a validated configuration.

Orders, fills, approvals, breakers, net values and differences are written
idempotently on their unique keys (never silently overwritten / duplicated);
the master row is idempotent on ``run_id``.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Insert, Select, Update, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from harbor.core.paper_domain import (
    CircuitBreakerState,
    PaperFill,
    PaperOrder,
    PaperStatus,
    RiskApproval,
)
from harbor.storage.models import (
    Base,
    CircuitBreaker,
    PaperNetValue,
    PaperReconciliationDifference,
    PaperRun,
)
from harbor.storage.models import (
    PaperFill as PaperFillModel,
)
from harbor.storage.models import (
    PaperOrder as PaperOrderModel,
)
from harbor.storage.models import (
    RiskApproval as RiskApprovalModel,
)

_PAPER_STATUSES = frozenset(status.value for status in PaperStatus)


def _order_row(order: PaperOrder) -> Mapping[str, Any]:
    """Map a domain order to its persisted column values (SP 4.6)."""
    return {
        "order_id": order.order_id,
        "market": order.market.value,
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": order.quantity,
        "currency": order.currency.value,
        "price_type": order.price_type.value,
        "status": order.status.value,
        "created_at": order.created_at,
        "intention_id": order.intention_id,
        "ref": order.ref or None,
    }


def _fill_row(fill: PaperFill) -> Mapping[str, Any]:
    """Map a domain fill to its persisted column values (SP 4.6)."""
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.paper_order_id,
        "market": fill.market.value,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "quantity": fill.quantity,
        "price": fill.price,
        "fee": fill.fee,
        "currency": fill.currency.value,
        "trade_date": fill.trade_date,
    }


def _approval_row(approval: RiskApproval) -> Mapping[str, Any]:
    """Map a domain approval to its persisted column values (SP 4.7)."""
    return {
        "approval_id": approval.approval_id,
        "scope": approval.scope,
        "approver": approval.approver,
        "decision": approval.decision.value,
        "rule": approval.rule,
        "reason": approval.reason,
        "decided_at": approval.decided_at,
    }


def _breaker_row(breaker: CircuitBreakerState) -> Mapping[str, Any]:
    """Map a domain circuit breaker to its persisted column values (SP 4.7)."""
    return {
        "breaker_id": breaker.breaker_id,
        "kind": breaker.kind.value,
        "triggered": breaker.triggered,
        "scope": breaker.scope,
        "reason": breaker.reason,
        "frozen_at": breaker.frozen_at,
        "recovered_at": breaker.recovered_at,
    }


class PaperRepository:
    """CRUD for the ``paper_runs`` master table and its artifacts."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    @staticmethod
    def _validate_status(status: str) -> str:
        """Return a status if it is part of the domain state machine, else raise."""
        if status not in _PAPER_STATUSES:
            raise ValueError(
                f"Unknown paper status {status!r}; expected one of {sorted(_PAPER_STATUSES)}."
            )
        return status

    def _create_run_statement(
        self,
        *,
        run_id: str,
        strategy: str,
        strategy_version: str,
        config_hash: str,
        config_snapshot: Mapping[str, Any],
        dataset_fingerprint: str,
        code_version: str,
        markets: Sequence[str],
        base_currency: str,
        created_at: datetime,
        status: str,
    ) -> Insert:
        """Build an idempotent insert keyed on ``run_id`` (SP 4.5)."""
        return (
            pg_insert(PaperRun)
            .values(
                {
                    "run_id": run_id,
                    "strategy": strategy,
                    "strategy_version": strategy_version,
                    "config_hash": config_hash,
                    "config_snapshot": dict(config_snapshot),
                    "dataset_fingerprint": dataset_fingerprint,
                    "code_version": code_version,
                    "markets": list(markets),
                    "base_currency": base_currency,
                    "status": self._validate_status(status),
                    "created_at": created_at,
                }
            )
            .on_conflict_do_nothing(index_elements=["run_id"])
        )

    def create_run(
        self,
        *,
        run_id: str,
        strategy: str,
        strategy_version: str,
        config_hash: str,
        config_snapshot: Mapping[str, Any],
        dataset_fingerprint: str,
        code_version: str,
        markets: Sequence[str],
        base_currency: str,
        created_at: datetime,
        status: str = PaperStatus.DRAFT.value,
    ) -> int:
        """Insert a paper-run master record, idempotent on ``run_id`` (SP 4.5).

        An existing run with the same id is left untouched so a re-run never
        silently overwrites a recorded paper run. Returns the number of rows
        actually inserted.
        """
        statement = self._create_run_statement(
            run_id=run_id,
            strategy=strategy,
            strategy_version=strategy_version,
            config_hash=config_hash,
            config_snapshot=config_snapshot,
            dataset_fingerprint=dataset_fingerprint,
            code_version=code_version,
            markets=markets,
            base_currency=base_currency,
            created_at=created_at,
            status=status,
        )
        result = self._connection.execute(statement.returning(PaperRun.run_id))
        return len(result.fetchall())

    def _update_run_statement(
        self,
        *,
        run_id: str,
        status: str,
        started_at: datetime | None,
        stopped_at: datetime | None,
    ) -> Update:
        """Build an update for a run's lifecycle status and timestamps (SP 4.10)."""
        return (
            update(PaperRun)
            .where(PaperRun.run_id == run_id)
            .values(
                status=self._validate_status(status),
                started_at=started_at,
                stopped_at=stopped_at,
            )
        )

    def update_run(
        self,
        *,
        run_id: str,
        status: str,
        started_at: datetime | None = None,
        stopped_at: datetime | None = None,
    ) -> int:
        """Update a paper run's lifecycle status and timestamps (SP 4.10).

        Returns the number of rows updated (0 if the run does not exist).
        """
        statement = self._update_run_statement(
            run_id=run_id,
            status=status,
            started_at=started_at,
            stopped_at=stopped_at,
        )
        result = self._connection.execute(statement)
        return result.rowcount or 0

    def get_run(self, run_id: str) -> Select[Any]:
        """Return a query for a single paper run by id (audit lookup)."""
        return select(PaperRun).where(PaperRun.run_id == run_id)

    @staticmethod
    def _insert_rows_statement(
        model: type[Base],
        paper_run_id: str,
        rows: Sequence[Mapping[str, Any]],
        conflict_columns: Sequence[str],
    ) -> Insert | None:
        """Build an idempotent insert of artifact rows tagged with the run id.

        Each row is associated with ``paper_run_id`` by injection, so every
        order / fill / approval / breaker / net value is traceable back to its
        paper run. Rows whose unique key already exists are skipped
        (``on_conflict_do_nothing``) so re-recording never duplicates an
        artifact.
        """
        if not rows:
            return None
        values = [dict(row, paper_run_id=paper_run_id) for row in rows]
        return (
            pg_insert(model)
            .values(values)
            .on_conflict_do_nothing(index_elements=list(conflict_columns))
        )

    def _insert_rows(
        self,
        model: type[Base],
        paper_run_id: str,
        rows: Sequence[Mapping[str, Any]],
        conflict_columns: Sequence[str],
    ) -> int:
        """Execute an idempotent insert of artifact rows and return the row count."""
        statement = self._insert_rows_statement(model, paper_run_id, rows, conflict_columns)
        if statement is None:
            return 0
        result = self._connection.execute(statement)
        return result.rowcount or 0

    def insert_orders(self, paper_run_id: str, orders: Sequence[PaperOrder]) -> int:
        """Record paper orders, idempotent on ``(paper_run_id, order_id)`` (SP 4.6)."""
        return self._insert_rows(
            PaperOrderModel,
            paper_run_id,
            [_order_row(order) for order in orders],
            ("paper_run_id", "order_id"),
        )

    def insert_order(self, paper_run_id: str, order: PaperOrder) -> int:
        """Record a single paper order (SP 4.6)."""
        return self.insert_orders(paper_run_id, (order,))

    def list_orders(self, paper_run_id: str) -> Select[Any]:
        """Return a query for a run's orders, newest first (SP 4.6)."""
        return (
            select(PaperOrderModel)
            .where(PaperOrderModel.paper_run_id == paper_run_id)
            .order_by(PaperOrderModel.created_at.desc())
        )

    def insert_fills(self, paper_run_id: str, fills: Sequence[PaperFill]) -> int:
        """Record paper fills, idempotent on ``(paper_run_id, fill_id)`` (SP 4.6)."""
        return self._insert_rows(
            PaperFillModel,
            paper_run_id,
            [_fill_row(fill) for fill in fills],
            ("paper_run_id", "fill_id"),
        )

    def insert_fill(self, paper_run_id: str, fill: PaperFill) -> int:
        """Record a single paper fill (SP 4.6)."""
        return self.insert_fills(paper_run_id, (fill,))

    def list_fills(self, paper_run_id: str) -> Select[Any]:
        """Return a query for a run's fills, ordered by trade date (SP 4.6)."""
        return (
            select(PaperFillModel)
            .where(PaperFillModel.paper_run_id == paper_run_id)
            .order_by(PaperFillModel.trade_date.asc())
        )

    def insert_approvals(self, paper_run_id: str, approvals: Sequence[RiskApproval]) -> int:
        """Record risk approvals, idempotent on ``(paper_run_id, approval_id)`` (SP 4.7)."""
        return self._insert_rows(
            RiskApprovalModel,
            paper_run_id,
            [_approval_row(approval) for approval in approvals],
            ("paper_run_id", "approval_id"),
        )

    def insert_approval(self, paper_run_id: str, approval: RiskApproval) -> int:
        """Record a single risk approval (SP 4.7)."""
        return self.insert_approvals(paper_run_id, (approval,))

    def list_approvals(self, paper_run_id: str) -> Select[Any]:
        """Return a query for a run's approvals, newest first (SP 4.7)."""
        return (
            select(RiskApprovalModel)
            .where(RiskApprovalModel.paper_run_id == paper_run_id)
            .order_by(RiskApprovalModel.decided_at.desc())
        )

    def insert_circuit_breakers(
        self, paper_run_id: str, breakers: Sequence[CircuitBreakerState]
    ) -> int:
        """Record circuit breakers, idempotent on ``(paper_run_id, breaker_id)`` (SP 4.7)."""
        return self._insert_rows(
            CircuitBreaker,
            paper_run_id,
            [_breaker_row(breaker) for breaker in breakers],
            ("paper_run_id", "breaker_id"),
        )

    def insert_circuit_breaker(self, paper_run_id: str, breaker: CircuitBreakerState) -> int:
        """Record a single circuit breaker (SP 4.7)."""
        return self.insert_circuit_breakers(paper_run_id, (breaker,))

    def list_circuit_breakers(self, paper_run_id: str) -> Select[Any]:
        """Return a query for a run's circuit breakers (SP 4.7)."""
        return select(CircuitBreaker).where(CircuitBreaker.paper_run_id == paper_run_id)

    def insert_net_values(self, paper_run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Record daily net values, idempotent on ``(run, as_of_date, currency)`` (SP 4.8)."""
        return self._insert_rows(
            PaperNetValue,
            paper_run_id,
            rows,
            ("paper_run_id", "as_of_date", "currency"),
        )

    def list_net_values(self, paper_run_id: str) -> Select[Any]:
        """Return a query for a run's daily net values, by date (SP 4.8)."""
        return (
            select(PaperNetValue)
            .where(PaperNetValue.paper_run_id == paper_run_id)
            .order_by(PaperNetValue.as_of_date.asc())
        )

    def insert_reconciliation_differences(
        self, paper_run_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        """Record reconciliation differences, idempotent on ``(run, date, check)`` (SP 4.8)."""
        return self._insert_rows(
            PaperReconciliationDifference,
            paper_run_id,
            rows,
            ("paper_run_id", "as_of_date", "check_name"),
        )

    def list_reconciliation_differences(self, paper_run_id: str) -> Select[Any]:
        """Return a query for a run's reconciliation differences (SP 4.8)."""
        return select(PaperReconciliationDifference).where(
            PaperReconciliationDifference.paper_run_id == paper_run_id
        )
