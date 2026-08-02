"""Create dividends table.

Revision ID: 0003_create_dividends
Revises: 0002_create_daily_quotes
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_create_dividends"
down_revision: str | Sequence[str] | None = "0002_create_daily_quotes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the dividends table."""
    op.create_table(
        "dividends",
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.CheckConstraint("type IN ('regular', 'special')", name="ck_dividends_type"),
        sa.ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_dividends_security",
        ),
        sa.PrimaryKeyConstraint("market", "symbol", "ex_date", name="pk_dividends"),
    )


def downgrade() -> None:
    """Drop the dividends table."""
    op.drop_table("dividends")
