"""Add the missing unique constraint that makes paper fills idempotent (MVP 4 / SP 4.6).

Revision ID: 0026_paper_fills_unique
Revises: 0025_create_validation_events
Create Date: 2026-09-19

``PaperRepository.insert_fills`` has always inserted with
``ON CONFLICT (paper_run_id, fill_id) DO NOTHING`` and documented itself as
"idempotent on (paper_run_id, fill_id)", but no constraint backed that claim:
``0024_create_paper_tables`` created the equivalent unique constraint for orders,
approvals, circuit breakers, net values and reconciliation differences — and
missed ``paper_fills``. PostgreSQL therefore rejects the insert outright with
``there is no unique or exclusion constraint matching the ON CONFLICT
specification`` rather than treating a repeat as a no-op.

Found while making the write-gated suites runnable against a disposable database:
``tests/test_paper_empty_db_upgrade.py::test_paper_artifact_round_trip`` is the
test that exercises exactly this path, and it had never been run against a
migrated database because the suite is skipped without
``HARBOR_TEST_DATABASE_URL``.

The migration is additive and cannot fail on existing data: ``paper_fills`` is
written by no caller in the repository (the only references are the repository
wrappers themselves), so the table is empty. If that ever stops being true, a
violating row would make this migration fail loudly instead of silently dropping
a constraint — which is the outcome we want.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026_paper_fills_unique"
down_revision: str | None = "0025_create_validation_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_paper_fills_id"


def upgrade() -> None:
    """Add ``uq_paper_fills_id`` on ``(paper_run_id, fill_id)``."""
    op.create_unique_constraint(_CONSTRAINT, "paper_fills", ["paper_run_id", "fill_id"])


def downgrade() -> None:
    """Drop the constraint, returning the table to its 0024 shape."""
    op.drop_constraint(_CONSTRAINT, "paper_fills", type_="unique")
