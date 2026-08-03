"""Create ingestion runs table.

Revision ID: 0010_create_ingestion_runs
Revises: 0009_create_adjusted_factors
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_create_ingestion_runs"
down_revision: str | Sequence[str] | None = "0009_create_adjusted_factors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ingestion runs table."""
    op.create_table(
        "ingestion_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("market", sa.String(length=4), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("records_processed", sa.BigInteger(), nullable=False),
        sa.Column("errors", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "market IN ('HK', 'US', 'BOTH')",
            name="ck_ingestion_runs_market",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_ingestion_runs_status",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_ingestion_runs"),
    )


def downgrade() -> None:
    """Drop the ingestion runs table."""
    op.drop_table("ingestion_runs")
