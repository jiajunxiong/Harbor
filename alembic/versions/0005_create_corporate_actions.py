"""Create corporate actions table.

Revision ID: 0005_create_corporate_actions
Revises: 0004_create_financials
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_create_corporate_actions"
down_revision: str | Sequence[str] | None = "0004_create_financials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the corporate actions table."""
    op.create_table(
        "corporate_actions",
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("announce_date", sa.Date(), nullable=True),
        sa.Column("ex_date", sa.Date(), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "action_type IN ("
            "'split', 'consolidation', 'rights_issue', "
            "'merger', 'spin_off', 'tender_offer', 'dividend'"
            ")",
            name="ck_corporate_actions_action_type",
        ),
        sa.ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_corporate_actions_security",
        ),
        sa.PrimaryKeyConstraint(
            "market",
            "symbol",
            "action_id",
            name="pk_corporate_actions",
        ),
    )


def downgrade() -> None:
    """Drop the corporate actions table."""
    op.drop_table("corporate_actions")
