"""Add backtest runs resume link column.

Revision ID: 0022_add_backtest_runs_resume_of
Revises: 0021_create_factor_snapshots
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_add_backtest_runs_resume_of"
down_revision: str | Sequence[str] | None = "0021_create_factor_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record the original run a resumed run was created from (SP 2.70).

    A resumed run gets a fresh ``run_id`` and points back at the original run
    via ``resume_of``, so a failed or cancelled run is never silently
    continued under the same id.
    """
    op.add_column("backtest_runs", sa.Column("resume_of", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop the resume link column."""
    op.drop_column("backtest_runs", "resume_of")
