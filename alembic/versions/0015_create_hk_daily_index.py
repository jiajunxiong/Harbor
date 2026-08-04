"""Create Hong Kong market daily quotes composite index.

Revision ID: 0015_create_hk_daily_index
Revises: 0014_create_quality_summary_us
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_create_hk_daily_index"
down_revision: str | Sequence[str] | None = "0014_create_quality_summary_us"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Hong Kong market daily quotes composite index."""
    op.create_index(
        "ix_daily_quotes_hk_symbol_date",
        "daily_quotes",
        ["symbol", "date"],
        postgresql_where=sa.text("market = 'HK'"),
    )


def downgrade() -> None:
    """Drop the Hong Kong market daily quotes composite index."""
    op.drop_index("ix_daily_quotes_hk_symbol_date", table_name="daily_quotes")
