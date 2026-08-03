"""Create quality issues table.

Revision ID: 0012_create_quality_issues
Revises: 0011_create_raw_payloads
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_create_quality_issues"
down_revision: str | Sequence[str] | None = "0011_create_raw_payloads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the quality issues table."""
    op.create_table(
        "quality_issues",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("check_name", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "severity IN ('warning', 'error')",
            name="ck_quality_issues_severity",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ingestion_runs.run_id"],
            name="fk_quality_issues_ingestion_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quality_issues"),
    )


def downgrade() -> None:
    """Drop the quality issues table."""
    op.drop_table("quality_issues")
