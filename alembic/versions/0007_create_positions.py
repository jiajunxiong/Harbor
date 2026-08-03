"""Create positions table.

Revision ID: 0007_create_positions
Revises: 0006_create_action_terms
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_create_positions"
down_revision: str | Sequence[str] | None = "0006_create_action_terms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the positions table."""
    op.create_table(
        "positions",
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("cost_basis", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("market_value", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_positions_security",
        ),
        sa.PrimaryKeyConstraint("market", "symbol", "date", name="pk_positions"),
    )


def downgrade() -> None:
    """Drop the positions table."""
    op.drop_table("positions")
