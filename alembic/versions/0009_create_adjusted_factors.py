"""Create adjusted factors table.

Revision ID: 0009_create_adjusted_factors
Revises: 0008_create_equity_events
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_create_adjusted_factors"
down_revision: str | Sequence[str] | None = "0008_create_equity_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the adjusted factors table."""
    op.create_table(
        "adjusted_factors",
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "cumulative_factor",
            sa.Numeric(precision=20, scale=6),
            nullable=False,
        ),
        sa.Column("daily_factor", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["market", "symbol", "date"],
            ["daily_quotes.market", "daily_quotes.symbol", "daily_quotes.date"],
            name="fk_adjusted_factors_daily_quote",
        ),
        sa.PrimaryKeyConstraint(
            "market",
            "symbol",
            "date",
            name="pk_adjusted_factors",
        ),
    )


def downgrade() -> None:
    """Drop the adjusted factors table."""
    op.drop_table("adjusted_factors")
