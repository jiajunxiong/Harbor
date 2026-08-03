"""Create action terms table.

Revision ID: 0006_create_action_terms
Revises: 0005_create_corporate_actions
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_create_action_terms"
down_revision: str | Sequence[str] | None = "0005_create_corporate_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the action terms table."""
    op.create_table(
        "action_terms",
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("term_type", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "term_type IN ('ratio', 'price', 'option')",
            name="ck_action_terms_term_type",
        ),
        sa.ForeignKeyConstraint(
            ["market", "symbol", "action_id"],
            [
                "corporate_actions.market",
                "corporate_actions.symbol",
                "corporate_actions.action_id",
            ],
            name="fk_action_terms_corporate_action",
        ),
        sa.PrimaryKeyConstraint(
            "market",
            "symbol",
            "action_id",
            "term_type",
            name="pk_action_terms",
        ),
    )


def downgrade() -> None:
    """Drop the action terms table."""
    op.drop_table("action_terms")
