"""Create the paper-trading tables.

Revision ID: 0024_create_paper_tables
Revises: 0023_create_validation_tables
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024_create_paper_tables"
down_revision: str | Sequence[str] | None = "0023_create_validation_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the paper-trading (模拟盘) tables (MVP 4 / SP 4.5-4.8).

    ``paper_runs`` is the master record of a paper run (config hash, dataset
    fingerprint, code version, market scope, base currency and lifecycle
    status, SP 4.5 / 4.9); the orders, fills, risk approvals, circuit
    breakers, daily net values and reconciliation differences are each
    persisted in their own table and linked to ``paper_runs`` via
    ``paper_run_id`` (SP 4.6-4.8), so every paper artifact is traceable to
    its run.
    """
    op.create_table(
        "paper_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=32), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("markets", postgresql.JSONB(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'ACTIVE', 'PAUSED', 'CIRCUIT_BROKEN', 'STOPPED')",
            name="ck_paper_runs_status",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_paper_runs"),
    )
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_run_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intention_id", sa.String(length=64), nullable=True),
        sa.Column("ref", sa.String(length=64), nullable=True),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_paper_orders_side"),
        sa.CheckConstraint(
            "status IN ("
            "'CREATED', 'SUBMITTED', 'PARTIALLY_FILLED', 'FILLED', "
            "'CANCELLED', 'REJECTED'"
            ")",
            name="ck_paper_orders_status",
        ),
        sa.ForeignKeyConstraint(
            ["paper_run_id"],
            ["paper_runs.run_id"],
            name="fk_paper_orders_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_orders"),
        sa.UniqueConstraint("paper_run_id", "order_id", name="uq_paper_orders_id"),
        sa.Index("ix_paper_orders_run_created", "paper_run_id", "created_at"),
    )
    op.create_table(
        "paper_fills",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_run_id", sa.String(length=64), nullable=False),
        sa.Column("fill_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("fee", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_paper_fills_side"),
        sa.ForeignKeyConstraint(
            ["paper_run_id"],
            ["paper_runs.run_id"],
            name="fk_paper_fills_run",
        ),
        sa.ForeignKeyConstraint(
            ["paper_run_id", "order_id"],
            ["paper_orders.paper_run_id", "paper_orders.order_id"],
            name="fk_paper_fills_order",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_fills"),
        sa.Index("ix_paper_fills_run_date", "paper_run_id", "trade_date"),
    )
    op.create_table(
        "risk_approvals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_run_id", sa.String(length=64), nullable=False),
        sa.Column("approval_id", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("approver", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("rule", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED')",
            name="ck_risk_approvals_decision",
        ),
        sa.ForeignKeyConstraint(
            ["paper_run_id"],
            ["paper_runs.run_id"],
            name="fk_risk_approvals_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_risk_approvals"),
        sa.UniqueConstraint("paper_run_id", "approval_id", name="uq_risk_approvals_id"),
    )
    op.create_table(
        "circuit_breakers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_run_id", sa.String(length=64), nullable=False),
        sa.Column("breaker_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("triggered", sa.Boolean(), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('DAILY', 'MONTHLY', 'DRAWDOWN')",
            name="ck_circuit_breakers_kind",
        ),
        sa.ForeignKeyConstraint(
            ["paper_run_id"],
            ["paper_runs.run_id"],
            name="fk_circuit_breakers_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_circuit_breakers"),
        sa.UniqueConstraint("paper_run_id", "breaker_id", name="uq_circuit_breakers_id"),
    )
    op.create_table(
        "paper_net_values",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_run_id", sa.String(length=64), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("cash", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("securities_value", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("fees_paid", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("total_value", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["paper_run_id"],
            ["paper_runs.run_id"],
            name="fk_paper_net_values_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_net_values"),
        sa.UniqueConstraint(
            "paper_run_id",
            "as_of_date",
            "currency",
            name="uq_paper_net_values_day_currency",
        ),
    )
    op.create_table(
        "paper_reconciliation_differences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_run_id", sa.String(length=64), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("check_name", sa.String(length=64), nullable=False),
        sa.Column("expected", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("actual", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["paper_run_id"],
            ["paper_runs.run_id"],
            name="fk_paper_reconciliation_differences_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_reconciliation_differences"),
        sa.UniqueConstraint(
            "paper_run_id",
            "as_of_date",
            "check_name",
            name="uq_paper_reconciliation_differences_check",
        ),
    )


def downgrade() -> None:
    """Drop the paper-trading tables in reverse dependency order."""
    op.drop_table("paper_reconciliation_differences")
    op.drop_table("paper_net_values")
    op.drop_table("circuit_breakers")
    op.drop_table("risk_approvals")
    op.drop_table("paper_fills")
    op.drop_table("paper_orders")
    op.drop_table("paper_runs")
