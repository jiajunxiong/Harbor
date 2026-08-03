"""Create United States market daily quotes composite index.

Revision ID: 0016_create_us_daily_quotes_index
Revises: 0015_create_hk_daily_quotes_index
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_create_us_daily_quotes_index"
down_revision: str | Sequence[str] | None = "0015_create_hk_daily_quotes_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the United States market daily quotes composite index."""
    op.create_index(
        "ix_daily_quotes_us_symbol_date",
        "daily_quotes",
        ["symbol", "date"],
        postgresql_where=sa.text("market = 'US'"),
    )


def downgrade() -> None:
    """Drop the United States market daily quotes composite index."""
    op.drop_index("ix_daily_quotes_us_symbol_date", table_name="daily_quotes")
