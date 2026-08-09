"""SQLAlchemy database models for Harbor market data."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all Harbor database models."""


class Security(Base):
    """A listed Hong Kong or U.S. equity security."""

    __tablename__ = "securities"
    __table_args__ = (CheckConstraint("market IN ('HK', 'US')", name="ck_securities_market"),)

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    list_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delist_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class DailyQuote(Base):
    """End-of-day OHLCV data for a listed security."""

    __tablename__ = "daily_quotes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_daily_quotes_security",
        ),
        Index(
            "ix_daily_quotes_hk_symbol_date",
            "symbol",
            "date",
            postgresql_where=text("market = 'HK'"),
        ),
        Index(
            "ix_daily_quotes_us_symbol_date",
            "symbol",
            "date",
            postgresql_where=text("market = 'US'"),
        ),
    )

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    adjusted_close: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)


class Dividend(Base):
    """A cash dividend declared for a listed security."""

    __tablename__ = "dividends"
    __table_args__ = (
        CheckConstraint("type IN ('regular', 'special')", name="ck_dividends_type"),
        ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_dividends_security",
        ),
    )

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    ex_date: Mapped[date] = mapped_column(Date, primary_key=True)
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class Financial(Base):
    """Reported financial indicators for a listed security."""

    __tablename__ = "financials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_financials_security",
        ),
    )

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    fiscal_period: Mapped[str] = mapped_column(String(16), primary_key=True)
    disclosure_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    roe: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    net_income: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    total_equity: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    revenue: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)


class Fundamental(Base):
    """Reference valuation and dividend metrics for a listed security."""

    __tablename__ = "fundamentals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_fundamentals_security",
        ),
    )

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    dividend_yield: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    payout_ratio: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    pe_ratio: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    pb_ratio: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)


class CorporateAction(Base):
    """A corporate action announced for a listed security."""

    __tablename__ = "corporate_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ("
            "'split', 'consolidation', 'rights_issue', "
            "'merger', 'spin_off', 'tender_offer', 'dividend'"
            ")",
            name="ck_corporate_actions_action_type",
        ),
        ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_corporate_actions_security",
        ),
    )

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    announce_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)


class ActionTerm(Base):
    """A specific term of a corporate action."""

    __tablename__ = "action_terms"
    __table_args__ = (
        CheckConstraint(
            "term_type IN ('ratio', 'price', 'option')",
            name="ck_action_terms_term_type",
        ),
        ForeignKeyConstraint(
            ["market", "symbol", "action_id"],
            [
                "corporate_actions.market",
                "corporate_actions.symbol",
                "corporate_actions.action_id",
            ],
            name="fk_action_terms_corporate_action",
        ),
    )

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    term_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Position(Base):
    """A holdings snapshot for a listed security on a given date."""

    __tablename__ = "positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["market", "symbol"],
            ["securities.market", "securities.symbol"],
            name="fk_positions_security",
        ),
    )

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    quantity: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    cost_basis: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    market_value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)


class EquityEvent(Base):
    """The calculated entitlement produced by a corporate action on a position."""

    __tablename__ = "equity_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["market", "symbol", "position_date"],
            ["positions.market", "positions.symbol", "positions.date"],
            name="fk_equity_events_position",
        ),
        ForeignKeyConstraint(
            ["market", "symbol", "action_id"],
            [
                "corporate_actions.market",
                "corporate_actions.symbol",
                "corporate_actions.action_id",
            ],
            name="fk_equity_events_corporate_action",
        ),
    )

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    position_date: Mapped[date] = mapped_column(Date, primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entitled_quantity: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    cash_amount: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdjustedFactor(Base):
    """Adjustment factors used to compute adjusted prices for a listed security."""

    __tablename__ = "adjusted_factors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["market", "symbol", "date"],
            ["daily_quotes.market", "daily_quotes.symbol", "daily_quotes.date"],
            name="fk_adjusted_factors_daily_quote",
        ),
    )

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    cumulative_factor: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    daily_factor: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)


class FxRate(Base):
    """A daily foreign-exchange rate (MVP 2 / SP 2.12).

    ``rate`` is the number of ``to_currency`` units per one ``from_currency``
    unit on ``date``. ``quality`` marks whether the rate is ``official`` or
    ``estimated`` so downstream users can weigh its reliability.
    """

    __tablename__ = "fx_rates"
    __table_args__ = (
        CheckConstraint(
            "from_currency IN ('HKD', 'USD')",
            name="ck_fx_rates_from_currency",
        ),
        CheckConstraint(
            "to_currency IN ('HKD', 'USD')",
            name="ck_fx_rates_to_currency",
        ),
        CheckConstraint(
            "quality IN ('official', 'estimated')",
            name="ck_fx_rates_quality",
        ),
        CheckConstraint("rate > 0", name="ck_fx_rates_rate_positive"),
    )

    from_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    to_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)


class IngestionRun(Base):
    """A record of a single data-ingestion run."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "market IN ('HK', 'US', 'BOTH')",
            name="ck_ingestion_runs_market",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_ingestion_runs_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(4), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    records_processed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    errors: Mapped[str | None] = mapped_column(Text, nullable=True)


class RawPayload(Base):
    """A raw response payload captured during an ingestion run."""

    __tablename__ = "raw_payloads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"],
            ["ingestion_runs.run_id"],
            name="fk_raw_payloads_ingestion_run",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QualityIssue(Base):
    """A data-quality finding recorded for an ingestion run."""

    __tablename__ = "quality_issues"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('warning', 'error')",
            name="ck_quality_issues_severity",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["ingestion_runs.run_id"],
            name="fk_quality_issues_ingestion_run",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    check_name: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


class BacktestRun(Base):
    """A master record of one backtest run (MVP 2 / SP 2.6).

    Records the validated configuration snapshot, its stable hash (SP 2.5),
    the code version, the data cutoff and the run lifecycle so every research
    run is traceable and replayable. A run may span multiple markets (HK, US
    or cross-market), so the table is keyed by ``run_id`` alone. Status values
    mirror :class:`harbor.core.backtest_domain.BacktestStatus` (SP 2.46).
    """

    __tablename__ = "backtest_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('INITIALIZING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_backtest_runs_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    code_version: Mapped[str] = mapped_column(String(64), nullable=False)
    data_cutoff: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_of: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BacktestNetValue(Base):
    """A daily net-value snapshot for a backtest run (MVP 2 / SP 2.7)."""

    __tablename__ = "backtest_net_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_net_values_run",
        ),
        UniqueConstraint(
            "backtest_run_id",
            "as_of_date",
            "currency",
            name="uq_backtest_net_values_day_currency",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cash: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    securities_value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    fees_paid: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    total_value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)


class BacktestPosition(Base):
    """A daily position snapshot for a backtest run (MVP 2 / SP 2.7)."""

    __tablename__ = "backtest_positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_positions_run",
        ),
        UniqueConstraint(
            "backtest_run_id",
            "market",
            "symbol",
            "as_of_date",
            name="uq_backtest_positions_holding",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    average_cost: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class BacktestFill(Base):
    """An executed order (成交) for a backtest run (MVP 2 / SP 2.7)."""

    __tablename__ = "backtest_fills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_fills_run",
        ),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_backtest_fills_side"),
        Index("ix_backtest_fills_run_date", "backtest_run_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    fee: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    order_ref: Mapped[str] = mapped_column(String(64), nullable=False)


class BacktestRebalance(Base):
    """A rebalance event for a backtest run (MVP 2 / SP 2.7)."""

    __tablename__ = "backtest_rebalances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_rebalances_run",
        ),
        UniqueConstraint(
            "backtest_run_id",
            "market",
            "rebalance_date",
            name="uq_backtest_rebalances_day",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    rebalance_date: Mapped[date] = mapped_column(Date, nullable=False)
    ref: Mapped[str] = mapped_column(String(64), nullable=False)


class BacktestMetric(Base):
    """A performance metric for a backtest run (MVP 2 / SP 2.7)."""

    __tablename__ = "backtest_metrics"
    __table_args__ = (
        ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_metrics_run",
        ),
        UniqueConstraint(
            "backtest_run_id",
            "metric_name",
            "as_of_date",
            name="uq_backtest_metrics_name_date",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)


class BacktestRejectedTrade(Base):
    """A trade the backtest refused to execute, with the reason (MVP 2 / SP 2.7).

    Captures orders rejected by suspension, delisting, liquidity or other
    constraints so every non-execution is auditable (SP 2.40 / 2.41).
    """

    __tablename__ = "backtest_rejected_trades"
    __table_args__ = (
        ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_rejected_trades_run",
        ),
        CheckConstraint(
            "side IS NULL OR side IN ('BUY', 'SELL')",
            name="ck_backtest_rejected_trades_side",
        ),
        Index("ix_backtest_rejected_trades_run_market", "backtest_run_id", "market"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str | None] = mapped_column(String(4), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    order_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BacktestFactorSnapshot(Base):
    """A per-symbol factor snapshot for one rebalance (MVP 2 / SP 2.28).

    Captures, for every symbol considered at a rebalance, the raw factor
    values, each input's availability date, the standardized scores, the
    composite score, the within-market rank/selection and the exclusion reason
    (SP 2.23). The JSONB columns store JSON-compatible objects, so dates are
    serialized as ISO strings.
    """

    __tablename__ = "backtest_factor_snapshots"
    __table_args__ = (
        CheckConstraint(
            "market IN ('HK', 'US')",
            name="ck_backtest_factor_snapshots_market",
        ),
        ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_factor_snapshots_run",
        ),
        UniqueConstraint(
            "backtest_run_id",
            "market",
            "symbol",
            "as_of_date",
            name="uq_backtest_factor_snapshots_symbol",
        ),
        Index("ix_backtest_factor_snapshots_run_date", "backtest_run_id", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    availability_dates: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    standardized_scores: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    composite_score: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ValidationRun(Base):
    """A master record of one out-of-sample validation run (MVP 3 / SP 3.12).

    Records the frozen validation configuration snapshot, its stable hash
    (SP 3.3), the code version and the lifecycle status (SP 3.13 state
    machine) so every validation run is traceable and replayable. A run may
    span multiple markets (HK, US or cross-market), so the table is keyed by
    ``run_id`` alone. ``test_set_id`` links the run to its registered
    independent holdout version (SP 3.5 / 3.42) once one is assigned.
    """

    __tablename__ = "validation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'DRAFT', 'DATA_FROZEN', 'TUNING', 'TEST_LOCKED', "
            "'EVALUATED', 'NOT_QUALIFIED', 'FAILED'"
            ")",
            name="ck_validation_runs_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    code_version: Mapped[str] = mapped_column(String(64), nullable=False)
    test_set_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class ValidationManifest(Base):
    """The frozen dataset manifest of a validation run (MVP 3 / SP 3.12).

    One row per run (keyed by ``validation_run_id``) mirroring the SP 3.6
    ``DatasetManifest``: markets, base currency, query boundaries, data
    cutoff, config/code/calendar/FX versions, the SP 3.7 fingerprint and the
    per-component query records (JSONB list of dicts). The fingerprint ties a
    run's artifacts to exactly the data they were computed from.
    """

    __tablename__ = "validation_manifests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_manifests_run",
        ),
    )

    validation_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    markets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    data_cutoff: Mapped[date] = mapped_column(Date, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calendar_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fx_source: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    random_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    components: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)


class ValidationSplit(Base):
    """The frozen train / validation / test split of a validation run (SP 3.12).

    One row per run mirroring the SP 3.4 ``EvaluationSplit``. The boundary
    ordering ``train_end < validation_start <= validation_end < test_start``
    is enforced at the domain level before persistence (SP 3.1); ``split_hash``
    records the SP 3.3 stable hash of the frozen split configuration.
    """

    __tablename__ = "validation_splits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_splits_run",
        ),
    )

    validation_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    split_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    train_start: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date] = mapped_column(Date, nullable=False)
    validation_start: Mapped[date] = mapped_column(Date, nullable=False)
    validation_end: Mapped[date] = mapped_column(Date, nullable=False)
    test_start: Mapped[date] = mapped_column(Date, nullable=False)
    test_end: Mapped[date] = mapped_column(Date, nullable=False)


class ValidationTrial(Base):
    """A recorded parameter trial of a validation run (MVP 3 / SP 3.12).

    One row per ``(validation_run_id, trial_id)`` mirroring the SP 3.18
    ``ParameterTrial``: parameters (JSONB list), the train / validation
    boundaries it used, the dataset fingerprint, seed, code version and the
    resulting validation metric or failure reason. ``backtest_run_id`` links
    the trial to the MVP 2 backtest run that produced its metric (SP 3.12
    acceptance), or is NULL for a failed trial that never ran.
    """

    __tablename__ = "validation_trials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_trials_run",
        ),
        ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_validation_trials_backtest_run",
        ),
        UniqueConstraint("validation_run_id", "trial_id", name="uq_validation_trials_trial"),
        Index("ix_validation_trials_run", "validation_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trial_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    train_start: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date] = mapped_column(Date, nullable=False)
    validation_start: Mapped[date] = mapped_column(Date, nullable=False)
    validation_end: Mapped[date] = mapped_column(Date, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    code_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    failed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    backtest_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ValidationFold(Base):
    """A rolling out-of-sample fold of a validation run (MVP 3 / SP 3.12).

    One row per ``(validation_run_id, fold_index)`` mirroring the SP 3.31
    ``WalkForwardFold``: its train / validation / test intervals, retraining
    anchor, dataset fingerprint and the MVP 2 ``backtest_run_id`` that holds
    the fold's OOS execution (SP 3.35). Every fold is traceable back to the
    data manifest, parameter selection and MVP 2 run (SP 3.36).
    """

    __tablename__ = "validation_folds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_folds_run",
        ),
        ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_validation_folds_backtest_run",
        ),
        UniqueConstraint("validation_run_id", "fold_index", name="uq_validation_folds_index"),
        Index("ix_validation_folds_run", "validation_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    fold_index: Mapped[int] = mapped_column(Integer, nullable=False)
    train_start: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date] = mapped_column(Date, nullable=False)
    validation_start: Mapped[date] = mapped_column(Date, nullable=False)
    validation_end: Mapped[date] = mapped_column(Date, nullable=False)
    test_start: Mapped[date] = mapped_column(Date, nullable=False)
    test_end: Mapped[date] = mapped_column(Date, nullable=False)
    retrain_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    backtest_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ValidationStressResult(Base):
    """A pre-registered stress scenario result of a validation run (SP 3.12).

    One row per ``(validation_run_id, scenario_name)`` recording the scenario
    assumptions (JSONB), applicable markets, the run fingerprint and the
    baseline / stressed MVP 2 backtest run ids so every stress scenario is
    auditable and replayable (SP 3.59). ``delta`` holds the quantified impact
    versus the baseline.
    """

    __tablename__ = "validation_stress_results"
    __table_args__ = (
        CheckConstraint(
            "scenario_type IN ("
            "'cost', 'liquidity', 'fx', 'calendar', "
            "'corporate_action', 'stock_pool', 'parameter_neighborhood'"
            ")",
            name="ck_validation_stress_results_type",
        ),
        ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_stress_results_run",
        ),
        ForeignKeyConstraint(
            ["baseline_backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_validation_stress_baseline_run",
        ),
        ForeignKeyConstraint(
            ["stressed_backtest_run_id"],
            ["backtest_runs.run_id"],
            name="fk_validation_stress_stressed_run",
        ),
        UniqueConstraint(
            "validation_run_id",
            "scenario_name",
            name="uq_validation_stress_results_scenario",
        ),
        Index("ix_validation_stress_results_run", "validation_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_name: Mapped[str] = mapped_column(String(128), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(32), nullable=False)
    assumptions: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    applicable_markets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    run_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_backtest_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stressed_backtest_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delta: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ValidationConclusion(Base):
    """The recorded out-of-sample conclusion of a validation run (SP 3.12).

    One row per run keyed by ``validation_run_id``: the pre-registered
    ``QUALIFIED`` / ``NOT_QUALIFIED`` / ``INCONCLUSIVE`` outcome (SP 3.58),
    the conclusion-rule version, the evidence chain (JSONB) and unresolved
    limitations (JSONB list), plus the timestamp it was recorded. Conclusions
    never carry a return promise (SP 3.87).
    """

    __tablename__ = "validation_conclusions"
    __table_args__ = (
        CheckConstraint(
            "conclusion IN ('QUALIFIED', 'NOT_QUALIFIED', 'INCONCLUSIVE')",
            name="ck_validation_conclusions_conclusion",
        ),
        ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_conclusions_run",
        ),
    )

    validation_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conclusion: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ValidationWarning(Base):
    """An audit warning recorded for a validation run (MVP 3 / SP 3.12).

    Append-only rows tagged with ``validation_run_id``: a warning code, the
    severity (``warning`` / ``error``), a human-readable message, optional
    JSON context and the timestamp. Warnings surface coverage gaps, stress
    losses and drift so a conclusion can never silently ignore them.
    """

    __tablename__ = "validation_warnings"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('warning', 'error')",
            name="ck_validation_warnings_severity",
        ),
        ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.run_id"],
            name="fk_validation_warnings_run",
        ),
        Index("ix_validation_warnings_run", "validation_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    warning_code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


v_quality_summary_hk = Table(
    "v_quality_summary_hk",
    Base.metadata,
    Column("check_name", String(128)),
    Column("severity", String(16)),
    Column("issue_count", Integer),
    Column("resolved_count", Integer),
    Column("unresolved_count", Integer),
)

v_quality_summary_us = Table(
    "v_quality_summary_us",
    Base.metadata,
    Column("check_name", String(128)),
    Column("severity", String(16)),
    Column("issue_count", Integer),
    Column("resolved_count", Integer),
    Column("unresolved_count", Integer),
)
