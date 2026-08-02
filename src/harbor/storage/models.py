"""SQLAlchemy database models for Harbor market data."""

from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Numeric,
    String,
    true,
)
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
