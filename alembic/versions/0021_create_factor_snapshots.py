"""Create factor snapshot table.

Revision ID: 0021_create_factor_snapshots
Revises: 0020_create_fx_rates
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021_create_factor_snapshots"
down_revision: str | Sequence[str] | None = "0020_create_fx_rates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the per-symbol factor snapshot table, linked to backtest_runs.

    One row per (market, symbol, as_of_date) captures the artifacts of SP 2.28:
    raw factor values, input availability dates, standardized scores, composite
    score, within-market rank/selection and the exclusion reason. The JSONB
    columns store JSON-compatible objects (dates as ISO strings).
    """
    op.create_table(
        "backtest_factor_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("backtest_run_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("raw_values", postgresql.JSONB(), nullable=False),
        sa.Column("availability_dates", postgresql.JSONB(), nullable=False),
        sa.Column("standardized_scores", postgresql.JSONB(), nullable=False),
        sa.Column("composite_score", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("exclusion_reason", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "market IN ('HK', 'US')",
            name="ck_backtest_factor_snapshots_market",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_factor_snapshots_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_factor_snapshots"),
        sa.UniqueConstraint(
            "backtest_run_id",
            "market",
            "symbol",
            "as_of_date",
            name="uq_backtest_factor_snapshots_symbol",
        ),
        sa.Index(
            "ix_backtest_factor_snapshots_run_date",
            "backtest_run_id",
            "as_of_date",
        ),
    )


def downgrade() -> None:
    """Drop the factor snapshot table."""
    op.drop_table("backtest_factor_snapshots")
