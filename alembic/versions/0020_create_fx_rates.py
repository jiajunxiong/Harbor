"""Create fx rates table.

Revision ID: 0020_create_fx_rates
Revises: 0019_add_financials_disclosure
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_create_fx_rates"
down_revision: str | Sequence[str] | None = "0019_add_financials_disclosure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the daily foreign-exchange rates table."""
    op.create_table(
        "fx_rates",
        sa.Column("from_currency", sa.String(length=3), nullable=False),
        sa.Column("to_currency", sa.String(length=3), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("quality", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "from_currency IN ('HKD', 'USD')",
            name="ck_fx_rates_from_currency",
        ),
        sa.CheckConstraint(
            "to_currency IN ('HKD', 'USD')",
            name="ck_fx_rates_to_currency",
        ),
        sa.CheckConstraint(
            "quality IN ('official', 'estimated')",
            name="ck_fx_rates_quality",
        ),
        sa.CheckConstraint("rate > 0", name="ck_fx_rates_rate_positive"),
        sa.PrimaryKeyConstraint(
            "from_currency",
            "to_currency",
            "date",
            name="pk_fx_rates",
        ),
    )


def downgrade() -> None:
    """Drop the foreign-exchange rates table."""
    op.drop_table("fx_rates")
