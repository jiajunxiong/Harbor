"""Create backtest result tables.

Revision ID: 0018_create_backtest_results
Revises: 0017_create_backtest_runs
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_create_backtest_results"
down_revision: str | Sequence[str] | None = "0017_create_backtest_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the backtest result tables, each linked to backtest_runs."""
    op.create_table(
        "backtest_net_values",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("backtest_run_id", sa.String(length=64), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("cash", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("securities_value", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("fees_paid", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("total_value", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_net_values_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_net_values"),
        sa.UniqueConstraint(
            "backtest_run_id",
            "as_of_date",
            "currency",
            name="uq_backtest_net_values_day_currency",
        ),
    )
    op.create_table(
        "backtest_positions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("backtest_run_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("average_cost", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_positions_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_positions"),
        sa.UniqueConstraint(
            "backtest_run_id",
            "market",
            "symbol",
            "as_of_date",
            name="uq_backtest_positions_holding",
        ),
    )
    op.create_table(
        "backtest_fills",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("backtest_run_id", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("fee", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("order_ref", sa.String(length=64), nullable=False),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_backtest_fills_side"),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_fills_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_fills"),
        sa.Index("ix_backtest_fills_run_date", "backtest_run_id", "trade_date"),
    )
    op.create_table(
        "backtest_rebalances",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("backtest_run_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("rebalance_date", sa.Date(), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_rebalances_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_rebalances"),
        sa.UniqueConstraint(
            "backtest_run_id",
            "market",
            "rebalance_date",
            name="uq_backtest_rebalances_day",
        ),
    )
    op.create_table(
        "backtest_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("backtest_run_id", sa.String(length=64), nullable=False),
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("value", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_metrics_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_metrics"),
        sa.UniqueConstraint(
            "backtest_run_id",
            "metric_name",
            "as_of_date",
            name="uq_backtest_metrics_name_date",
        ),
    )
    op.create_table(
        "backtest_rejected_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("backtest_run_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("order_ref", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "side IS NULL OR side IN ('BUY', 'SELL')",
            name="ck_backtest_rejected_trades_side",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_rejected_trades_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_rejected_trades"),
        sa.Index("ix_backtest_rejected_trades_run_market", "backtest_run_id", "market"),
    )


def downgrade() -> None:
    """Drop the backtest result tables."""
    op.drop_table("backtest_rejected_trades")
    op.drop_table("backtest_metrics")
    op.drop_table("backtest_rebalances")
    op.drop_table("backtest_fills")
    op.drop_table("backtest_positions")
    op.drop_table("backtest_net_values")
