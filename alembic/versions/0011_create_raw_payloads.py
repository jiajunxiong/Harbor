"""Create raw payloads table.

Revision ID: 0011_create_raw_payloads
Revises: 0010_create_ingestion_runs
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_create_raw_payloads"
down_revision: str | Sequence[str] | None = "0010_create_ingestion_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the raw payloads table."""
    op.create_table(
        "raw_payloads",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ingestion_runs.run_id"],
            name="fk_raw_payloads_ingestion_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_raw_payloads"),
    )


def downgrade() -> None:
    """Drop the raw payloads table."""
    op.drop_table("raw_payloads")
