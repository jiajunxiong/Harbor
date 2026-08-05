"""Create backtest runs table.

Revision ID: 0017_create_backtest_runs
Revises: 0016_create_us_daily_index
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_create_backtest_runs"
down_revision: str | Sequence[str] | None = "0016_create_us_daily_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the backtest runs master table."""
    op.create_table(
        "backtest_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=32), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("data_cutoff", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('INITIALIZING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_backtest_runs_status",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_backtest_runs"),
    )


def downgrade() -> None:
    """Drop the backtest runs table."""
    op.drop_table("backtest_runs")
