"""Create out-of-sample validation tables.

Revision ID: 0023_create_validation_tables
Revises: 0022_add_backtest_runs_resume_of
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_create_validation_tables"
down_revision: str | Sequence[str] | None = "0022_add_backtest_runs_resume_of"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the out-of-sample validation tables (MVP 3 / SP 3.12).

    ``validation_runs`` is the master record of a validation run; the frozen
    artifacts (dataset manifest, split) and the recorded outcomes (trials,
    folds, stress results, conclusion, warnings) are each persisted in their
    own table and linked to ``validation_runs``. Trials, folds and stress
    results that correspond to an executed MVP 2 backtest additionally link
    to ``backtest_runs`` via ``backtest_run_id`` (SP 3.12 acceptance), so
    every validation artifact is traceable to the research run that produced
    it. JSONB columns store JSON-compatible objects (dates as ISO strings).
    """
    op.create_table(
        "validation_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("test_set_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ("
            "'DRAFT', 'DATA_FROZEN', 'TUNING', 'TEST_LOCKED', "
            "'EVALUATED', 'NOT_QUALIFIED', 'FAILED'"
            ")",
            name="ck_validation_runs_status",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_validation_runs"),
    )
    op.create_table(
        "validation_manifests",
        sa.Column("validation_run_id", sa.String(length=64), nullable=False),
        sa.Column("markets", postgresql.JSONB(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("data_cutoff", sa.Date(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("calendar_version", sa.String(length=64), nullable=False),
        sa.Column("fx_source", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=True),
        sa.Column("components", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_manifests_run",
        ),
        sa.PrimaryKeyConstraint("validation_run_id", name="pk_validation_manifests"),
    )
    op.create_table(
        "validation_splits",
        sa.Column("validation_run_id", sa.String(length=64), nullable=False),
        sa.Column("split_hash", sa.String(length=64), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("train_end", sa.Date(), nullable=False),
        sa.Column("validation_start", sa.Date(), nullable=False),
        sa.Column("validation_end", sa.Date(), nullable=False),
        sa.Column("test_start", sa.Date(), nullable=False),
        sa.Column("test_end", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_splits_run",
        ),
        sa.PrimaryKeyConstraint("validation_run_id", name="pk_validation_splits"),
    )
    op.create_table(
        "validation_trials",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("validation_run_id", sa.String(length=64), nullable=False),
        sa.Column("trial_id", sa.String(length=64), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("train_end", sa.Date(), nullable=False),
        sa.Column("validation_start", sa.Date(), nullable=False),
        sa.Column("validation_end", sa.Date(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("metric", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("failed_reason", sa.Text(), nullable=True),
        sa.Column("backtest_run_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_trials_run",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_validation_trials_backtest_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_validation_trials"),
        sa.UniqueConstraint(
            "validation_run_id",
            "trial_id",
            name="uq_validation_trials_trial",
        ),
        sa.Index("ix_validation_trials_run", "validation_run_id"),
    )
    op.create_table(
        "validation_folds",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("validation_run_id", sa.String(length=64), nullable=False),
        sa.Column("fold_index", sa.Integer(), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("train_end", sa.Date(), nullable=False),
        sa.Column("validation_start", sa.Date(), nullable=False),
        sa.Column("validation_end", sa.Date(), nullable=False),
        sa.Column("test_start", sa.Date(), nullable=False),
        sa.Column("test_end", sa.Date(), nullable=False),
        sa.Column("retrain_date", sa.Date(), nullable=True),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("backtest_run_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_folds_run",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_validation_folds_backtest_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_validation_folds"),
        sa.UniqueConstraint(
            "validation_run_id",
            "fold_index",
            name="uq_validation_folds_index",
        ),
        sa.Index("ix_validation_folds_run", "validation_run_id"),
    )
    op.create_table(
        "validation_stress_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("validation_run_id", sa.String(length=64), nullable=False),
        sa.Column("scenario_name", sa.String(length=128), nullable=False),
        sa.Column("scenario_type", sa.String(length=32), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(), nullable=False),
        sa.Column("applicable_markets", postgresql.JSONB(), nullable=False),
        sa.Column("run_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("baseline_backtest_run_id", sa.String(length=64), nullable=True),
        sa.Column("stressed_backtest_run_id", sa.String(length=64), nullable=True),
        sa.Column("delta", postgresql.JSONB(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "scenario_type IN ("
            "'cost', 'liquidity', 'fx', 'calendar', "
            "'corporate_action', 'stock_pool', 'parameter_neighborhood'"
            ")",
            name="ck_validation_stress_results_type",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_stress_results_run",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_validation_stress_baseline_run",
        ),
        sa.ForeignKeyConstraint(
            ["stressed_backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_validation_stress_stressed_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_validation_stress_results"),
        sa.UniqueConstraint(
            "validation_run_id",
            "scenario_name",
            name="uq_validation_stress_results_scenario",
        ),
        sa.Index("ix_validation_stress_results_run", "validation_run_id"),
    )
    op.create_table(
        "validation_conclusions",
        sa.Column("validation_run_id", sa.String(length=64), nullable=False),
        sa.Column("conclusion", sa.String(length=16), nullable=False),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("limitations", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "conclusion IN ('QUALIFIED', 'NOT_QUALIFIED', 'INCONCLUSIVE')",
            name="ck_validation_conclusions_conclusion",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_conclusions_run",
        ),
        sa.PrimaryKeyConstraint("validation_run_id", name="pk_validation_conclusions"),
    )
    op.create_table(
        "validation_warnings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("validation_run_id", sa.String(length=64), nullable=False),
        sa.Column("warning_code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "severity IN ('warning', 'error')",
            name="ck_validation_warnings_severity",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_warnings_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_validation_warnings"),
        sa.Index("ix_validation_warnings_run", "validation_run_id"),
    )


def downgrade() -> None:
    """Drop the out-of-sample validation tables in reverse dependency order."""
    op.drop_table("validation_warnings")
    op.drop_table("validation_conclusions")
    op.drop_table("validation_stress_results")
    op.drop_table("validation_folds")
    op.drop_table("validation_trials")
    op.drop_table("validation_splits")
    op.drop_table("validation_manifests")
    op.drop_table("validation_runs")
