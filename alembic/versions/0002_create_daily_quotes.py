"""Create daily quotes table.

Revision ID: 0002_create_daily_quotes
Revises: 0001_create_securities
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_create_daily_quotes"
down_revision: str | Sequence[str] | None = "0001_create_securities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the daily OHLCV quotes table."""
    op.create_table(
        "daily_quotes",
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("adjusted_close", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_daily_quotes_security",
        ),
        sa.PrimaryKeyConstraint("market", "symbol", "date", name="pk_daily_quotes"),
    )


def downgrade() -> None:
    """Drop the daily OHLCV quotes table."""
    op.drop_table("daily_quotes")
