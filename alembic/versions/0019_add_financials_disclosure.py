"""Add point-in-time disclosure date to financials.

Revision ID: 0019_add_financials_disclosure
Revises: 0018_create_backtest_results
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_add_financials_disclosure"
down_revision: str | Sequence[str] | None = "0018_create_backtest_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the financial disclosure date used for point-in-time availability."""
    op.add_column("financials", sa.Column("disclosure_date", sa.Date(), nullable=True))


def downgrade() -> None:
    """Drop the financial disclosure date column."""
    op.drop_column("financials", "disclosure_date")
