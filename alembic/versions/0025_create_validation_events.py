"""Add the validation lifecycle event table (MVP 5 / SP 5.26, SP 5.33).

Revision ID: 0025_create_validation_events
Revises: 0024_create_paper_tables
Create Date: 2026-09-19

Why this migration exists at all: the SP 3.13 state machine has always produced
a ``ValidationTransition`` for each move, but no code path persisted one. The
master row keeps only the *last* ``updated_at``, so a run's freeze time was lost
as soon as it advanced, and its audit trail did not exist outside process memory.
An append-only event row fixes both.

``from_status`` is nullable on purpose: creating a run is a state entry with no
predecessor, and recording it as ``DRAFT -> DRAFT`` would be a lie about what
happened.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_create_validation_events"
down_revision: str | None = "0024_create_paper_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the append-only validation event table."""
    op.create_table(
        "validation_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("validation_run_id", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_validation_events"),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_events_run",
        ),
    )
    op.create_index(
        "ix_validation_events_run",
        "validation_events",
        ["validation_run_id", "recorded_at"],
    )


def downgrade() -> None:
    """Drop the validation event table."""
    op.drop_index("ix_validation_events_run", table_name="validation_events")
    op.drop_table("validation_events")
