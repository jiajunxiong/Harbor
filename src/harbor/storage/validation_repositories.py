"""Repository for out-of-sample validation runs and artifacts (MVP 3 / SP 3.12).

A validation run spans multiple markets (HK, US or cross-market) and produces
many artifacts (frozen manifest, split, parameter trials, walk-forward folds,
stress results, a conclusion and audit warnings), so every operation is keyed
by ``validation_run_id`` and the artifacts that correspond to an executed MVP 2
backtest additionally link to ``backtest_run_id`` (SP 3.12 acceptance).

The status vocabulary mirrors :class:`harbor.core.validation_domain.ValidationStatus`
(SP 3.13), keeping the persisted state machine in sync with the domain.
``config_snapshot`` must be JSON-serializable (for example the result of
``ValidationConfig.model_dump(mode="json")``); the orchestration layer is
responsible for deriving it from a validated configuration.

The frozen 1:1 artifacts (manifest, split, conclusion) are written
idempotently on ``validation_run_id`` (never silently overwritten); trials,
folds and stress results are idempotent on their unique keys; warnings are
append-only events.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Insert, Select, Update, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from harbor.core.validation_domain import ValidationStatus
from harbor.storage.models import (
    Base,
    ValidationConclusion,
    ValidationFold,
    ValidationManifest,
    ValidationRun,
    ValidationSplit,
    ValidationStressResult,
    ValidationTrial,
    ValidationWarning,
)

_VALIDATION_STATUSES = frozenset(status.value for status in ValidationStatus)


class ValidationRepository:
    """CRUD for the ``validation_runs`` master table and its artifacts."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    @staticmethod
    def _validate_status(status: str) -> str:
        """Return a status if it is part of the domain state machine, else raise."""
        if status not in _VALIDATION_STATUSES:
            raise ValueError(
                f"Unknown validation status {status!r}; "
                f"expected one of {sorted(_VALIDATION_STATUSES)}."
            )
        return status

    def _create_statement(
        self,
        *,
        run_id: str,
        config_hash: str,
        config_snapshot: Mapping[str, Any],
        code_version: str,
        created_at: datetime,
        status: str,
        test_set_id: str | None = None,
    ) -> Insert:
        """Build an idempotent insert keyed on ``run_id``."""
        return (
            pg_insert(ValidationRun)
            .values(
                {
                    "run_id": run_id,
                    "config_hash": config_hash,
                    "config_snapshot": dict(config_snapshot),
                    "code_version": code_version,
                    "test_set_id": test_set_id,
                    "status": self._validate_status(status),
                    "created_at": created_at,
                }
            )
            .on_conflict_do_nothing(index_elements=["run_id"])
        )

    def _update_statement(
        self,
        *,
        run_id: str,
        status: str,
        updated_at: datetime | None,
        error_summary: str | None,
    ) -> Update:
        """Build an update for a run's lifecycle status and diagnostics."""
        return (
            update(ValidationRun)
            .where(ValidationRun.run_id == run_id)
            .values(
                status=self._validate_status(status),
                updated_at=updated_at,
                error_summary=error_summary,
            )
        )

    def create_run(
        self,
        *,
        run_id: str,
        config_hash: str,
        config_snapshot: Mapping[str, Any],
        code_version: str,
        created_at: datetime,
        status: str = ValidationStatus.DRAFT.value,
        test_set_id: str | None = None,
    ) -> int:
        """Insert a validation-run master record, idempotent on ``run_id``.

        An existing run with the same id is left untouched: a re-run must not
        silently overwrite a frozen validation (SP 3.12). Returns the number
        of rows actually inserted.
        """
        statement = self._create_statement(
            run_id=run_id,
            config_hash=config_hash,
            config_snapshot=config_snapshot,
            code_version=code_version,
            created_at=created_at,
            status=status,
            test_set_id=test_set_id,
        )
        result = self._connection.execute(statement.returning(ValidationRun.run_id))
        return len(result.fetchall())

    def update_run(
        self,
        *,
        run_id: str,
        status: str,
        updated_at: datetime | None = None,
        error_summary: str | None = None,
    ) -> int:
        """Update a run's lifecycle status and optional diagnostics.

        Returns the number of rows updated (0 if the run does not exist).
        """
        statement = self._update_statement(
            run_id=run_id,
            status=status,
            updated_at=updated_at,
            error_summary=error_summary,
        )
        result = self._connection.execute(statement)
        return result.rowcount or 0

    def get_run(self, run_id: str) -> Select[Any]:
        """Return a query for a single run by id (audit lookup)."""
        return select(ValidationRun).where(ValidationRun.run_id == run_id)

    @staticmethod
    def _upsert_on_run_statement(
        model: type[Base],
        validation_run_id: str,
        values: Mapping[str, Any],
    ) -> Insert:
        """Build an idempotent insert keyed on ``validation_run_id``.

        The frozen 1:1 artifacts (manifest / split / conclusion) are written
        once per run; a repeated write leaves the existing row untouched so a
        frozen boundary or fingerprint can never be silently overwritten.
        """
        return (
            pg_insert(model)
            .values(dict(values, validation_run_id=validation_run_id))
            .on_conflict_do_nothing(index_elements=["validation_run_id"])
        )

    def upsert_manifest(self, validation_run_id: str, values: Mapping[str, Any]) -> int:
        """Record a run's frozen dataset manifest (SP 3.12)."""
        statement = self._upsert_on_run_statement(ValidationManifest, validation_run_id, values)
        result = self._connection.execute(statement.returning(ValidationManifest.validation_run_id))
        return len(result.fetchall())

    def upsert_split(self, validation_run_id: str, values: Mapping[str, Any]) -> int:
        """Record a run's frozen train / validation / test split (SP 3.12)."""
        statement = self._upsert_on_run_statement(ValidationSplit, validation_run_id, values)
        result = self._connection.execute(statement.returning(ValidationSplit.validation_run_id))
        return len(result.fetchall())

    def upsert_conclusion(self, validation_run_id: str, values: Mapping[str, Any]) -> int:
        """Record a run's out-of-sample conclusion (SP 3.12)."""
        statement = self._upsert_on_run_statement(ValidationConclusion, validation_run_id, values)
        result = self._connection.execute(
            statement.returning(ValidationConclusion.validation_run_id)
        )
        return len(result.fetchall())

    @staticmethod
    def _insert_rows_statement(
        model: type[Base],
        validation_run_id: str,
        rows: Sequence[Mapping[str, Any]],
        conflict_columns: Sequence[str],
    ) -> Insert | None:
        """Build an append-only insert of artifact rows tagged with the run id.

        Each row is associated with ``validation_run_id`` by injection, so
        every trial / fold / stress result / warning is traceable back to its
        validation run. Rows whose unique key already exists are skipped
        (``on_conflict_do_nothing``) so re-running the same experiment never
        duplicates a recorded artifact.
        """
        if not rows:
            return None
        values = [dict(row, validation_run_id=validation_run_id) for row in rows]
        return (
            pg_insert(model)
            .values(values)
            .on_conflict_do_nothing(index_elements=list(conflict_columns))
        )

    def _insert_rows(
        self,
        model: type[Base],
        validation_run_id: str,
        rows: Sequence[Mapping[str, Any]],
        conflict_columns: Sequence[str],
    ) -> int:
        """Execute an idempotent insert of artifact rows and return the row count."""
        statement = self._insert_rows_statement(model, validation_run_id, rows, conflict_columns)
        if statement is None:
            return 0
        result = self._connection.execute(statement)
        return result.rowcount or 0

    def insert_trials(self, validation_run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Record parameter trials, idempotent on ``(validation_run_id, trial_id)``."""
        return self._insert_rows(
            ValidationTrial, validation_run_id, rows, ("validation_run_id", "trial_id")
        )

    def insert_folds(self, validation_run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Record walk-forward folds, idempotent on ``(validation_run_id, fold_index)``."""
        return self._insert_rows(
            ValidationFold, validation_run_id, rows, ("validation_run_id", "fold_index")
        )

    def insert_stress_results(
        self, validation_run_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        """Record stress scenario results, idempotent on scenario name."""
        return self._insert_rows(
            ValidationStressResult,
            validation_run_id,
            rows,
            ("validation_run_id", "scenario_name"),
        )

    def insert_warnings(self, validation_run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Record append-only audit warnings for a run."""
        return self._insert_rows(ValidationWarning, validation_run_id, rows, ("id",))

    def get_manifest(self, validation_run_id: str) -> Select[Any]:
        """Return a query for a run's frozen dataset manifest."""
        return select(ValidationManifest).where(
            ValidationManifest.validation_run_id == validation_run_id
        )

    def get_split(self, validation_run_id: str) -> Select[Any]:
        """Return a query for a run's frozen split."""
        return select(ValidationSplit).where(ValidationSplit.validation_run_id == validation_run_id)

    def get_conclusion(self, validation_run_id: str) -> Select[Any]:
        """Return a query for a run's recorded conclusion."""
        return select(ValidationConclusion).where(
            ValidationConclusion.validation_run_id == validation_run_id
        )

    def list_trials(self, validation_run_id: str) -> Select[Any]:
        """Return a run-scoped trials query."""
        return select(ValidationTrial).where(ValidationTrial.validation_run_id == validation_run_id)

    def list_folds(self, validation_run_id: str) -> Select[Any]:
        """Return a run-scoped folds query."""
        return select(ValidationFold).where(ValidationFold.validation_run_id == validation_run_id)

    def list_stress_results(self, validation_run_id: str) -> Select[Any]:
        """Return a run-scoped stress results query."""
        return select(ValidationStressResult).where(
            ValidationStressResult.validation_run_id == validation_run_id
        )

    def list_warnings(self, validation_run_id: str) -> Select[Any]:
        """Return a run-scoped warnings query."""
        return select(ValidationWarning).where(
            ValidationWarning.validation_run_id == validation_run_id
        )
