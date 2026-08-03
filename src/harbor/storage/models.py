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
    Integer,
    Numeric,
    String,
    Table,
    Text,
    false,
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
