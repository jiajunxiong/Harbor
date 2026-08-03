"""Create financials table.

Revision ID: 0004_create_financials
Revises: 0003_create_dividends
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_create_financials"
down_revision: str | Sequence[str] | None = "0003_create_dividends"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the financial indicators and fundamentals tables."""
    op.create_table(
        "financials",
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=16), nullable=False),
        sa.Column("roe", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("net_income", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("total_equity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("revenue", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_financials_security",
        ),
        sa.PrimaryKeyConstraint(
            "market",
            "symbol",
            "report_date",
            "fiscal_period",
            name="pk_financials",
        ),
    )
    op.create_table(
        "fundamentals",
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("dividend_yield", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("payout_ratio", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("pe_ratio", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("pb_ratio", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_fundamentals_security",
        ),
        sa.PrimaryKeyConstraint("market", "symbol", "date", name="pk_fundamentals"),
    )


def downgrade() -> None:
    """Drop the financial indicators and fundamentals tables."""
    op.drop_table("fundamentals")
    op.drop_table("financials")
