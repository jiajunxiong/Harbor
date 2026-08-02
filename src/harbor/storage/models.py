"""SQLAlchemy database models for Harbor market data."""

from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, String, true
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
