"""Create equity events table.

Revision ID: 0008_create_equity_events
Revises: 0007_create_positions
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_create_equity_events"
down_revision: str | Sequence[str] | None = "0007_create_positions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the equity events table."""
    op.create_table(
        "equity_events",
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("position_date", sa.Date(), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column(
            "entitled_quantity",
            sa.Numeric(precision=20, scale=6),
            nullable=False,
        ),
        sa.Column("cash_amount", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["market", "symbol", "position_date"],
            ["positions.market", "positions.symbol", "positions.date"],
            name="fk_equity_events_position",
        ),
        sa.ForeignKeyConstraint(
            ["market", "symbol", "action_id"],
            [
                "corporate_actions.market",
                "corporate_actions.symbol",
                "corporate_actions.action_id",
            ],
            name="fk_equity_events_corporate_action",
        ),
        sa.PrimaryKeyConstraint(
            "market",
            "symbol",
            "position_date",
            "action_id",
            name="pk_equity_events",
        ),
    )


def downgrade() -> None:
    """Drop the equity events table."""
    op.drop_table("equity_events")
