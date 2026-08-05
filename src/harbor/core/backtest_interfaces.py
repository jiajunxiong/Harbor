"""Abstract interfaces for the Harbor backtest engine (MVP 2 / SP 2.3).

This module defines the engine's contracts for data reading, the trading
calendar, signal generation, portfolio construction, fill simulation and
reporting. The core layer depends only on the immutable domain types from
:mod:`harbor.core.backtest_domain` (SP 2.2) and on other core modules — it
never imports storage repositories, database objects or CLI code.

Every interface is market-scoped and point-in-time aware (SP 2.9):
implementations may only expose data knowable on or before the relevant
decision date, and callers must never assume future information is present.

The data-record value types (``DailyQuote``, ``Dividend``,
``FundamentalRecord``, ``AdjustmentFactor``) are the reader's contract shapes.
They mirror the MVP 1 storage columns but are plain immutable domain records,
so the storage implementation is never leaked to the engine.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import (
    BacktestState,
    Currency,
    Fill,
    Market,
    NetValue,
    Order,
    Position,
)
from harbor.core.equity import EntitlementEvent


@dataclass(frozen=True)
class DailyQuote:
    """Point-in-time OHLCV quote for one symbol on one trading day."""

    market: Market
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    adjusted_close: float

    def __post_init__(self) -> None:
        if self.open < 0 or self.high < 0 or self.low < 0 or self.close < 0:
            raise ValueError("Quote prices must be non-negative.")
        if self.volume < 0:
            raise ValueError("Quote volume must be non-negative.")


@dataclass(frozen=True)
class Dividend:
    """A declared cash dividend (see SP 2.43).

    ``is_special`` marks special dividends, which the dividend-yield factor
    (SP 2.17) excludes from regular yield by default.
    """

    market: Market
    symbol: str
    amount: float
    currency: Currency
    ex_date: date
    record_date: date | None = None
    payment_date: date | None = None
    is_special: bool = False

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("Dividend amount must be non-negative.")


@dataclass(frozen=True)
class FundamentalRecord:
    """A reported financial snapshot with its point-in-time availability.

    ``available_on`` is the date the report became knowable (disclosure /
    announcement date). A ``None`` availability means the record cannot be
    safely dated; the point-in-time alignment (SP 2.9) must refuse such a
    record rather than silently use it, because using an undated report risks
    look-ahead.
    """

    market: Market
    symbol: str
    report_date: date
    fiscal_period: str
    available_on: date | None
    roe: float | None = None
    net_income: float | None = None
    total_equity: float | None = None
    revenue: float | None = None


@dataclass(frozen=True)
class AdjustmentFactor:
    """An adjusted-price factor that applies to quotes from ``date`` onward."""

    market: Market
    symbol: str
    date: date
    cumulative_factor: float
    daily_factor: float

    def __post_init__(self) -> None:
        if self.cumulative_factor <= 0:
            raise ValueError("Cumulative factor must be positive.")
        if self.daily_factor <= 0:
            raise ValueError("Daily factor must be positive.")


class BacktestDataReader(ABC):
    """Point-in-time data access for the backtest engine (SP 2.8).

    Implementations wrap the storage repositories but return typed domain
    records only, never database objects or row mappings. All reads are scoped
    by :class:`Market` so Hong Kong and United States data are never mixed.
    The universe returned by :meth:`list_securities` is historical
    (survivorship-bias free, SP 2.10): a symbol must appear only while it was
    actually listed and tradeable.
    """

    @abstractmethod
    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        """Return securities that were listed and active on ``as_of``."""
        raise NotImplementedError

    @abstractmethod
    def daily_quotes(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[DailyQuote]:
        """Return point-in-time daily quotes for a symbol within a range."""
        raise NotImplementedError

    @abstractmethod
    def dividends(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Dividend]:
        """Return declared dividends for a symbol within a date range."""
        raise NotImplementedError

    @abstractmethod
    def fundamentals(
        self,
        market: Market,
        symbol: str,
        as_of: date,
    ) -> Sequence[FundamentalRecord]:
        """Return fundamental records knowable on or before ``as_of``.

        Records whose availability date is unknown (``None``) are excluded
        rather than silently used (SP 2.9).
        """
        raise NotImplementedError

    @abstractmethod
    def corporate_actions(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[EntitlementEvent]:
        """Return corporate actions for a symbol within a date range."""
        raise NotImplementedError

    @abstractmethod
    def adjustment_factors(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[AdjustmentFactor]:
        """Return adjusted-price factors for a symbol within a date range."""
        raise NotImplementedError


class TradingCalendar(ABC):
    """HK/US trading and rebalance calendars (SP 2.11).

    Hong Kong and United States keep independent calendars, so every method
    is scoped by :class:`Market`. Rebalance-day deferral rules (SP 2.33) use
    :meth:`next_trading_day`; the resulting day-and-flag values are wrapped in
    :class:`~harbor.core.backtest_domain.TradingDay` by callers.
    """

    @abstractmethod
    def is_trading_day(self, market: Market, day: date) -> bool:
        """Return whether ``day`` is a trading day in ``market``."""
        raise NotImplementedError

    @abstractmethod
    def next_trading_day(self, market: Market, day: date) -> date:
        """Return the first trading day on or after ``day`` in ``market``."""
        raise NotImplementedError

    @abstractmethod
    def previous_trading_day(self, market: Market, day: date) -> date:
        """Return the last trading day on or before ``day`` in ``market``."""
        raise NotImplementedError

    @abstractmethod
    def trading_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        """Return trading days in ``market`` within the inclusive range."""
        raise NotImplementedError

    @abstractmethod
    def rebalance_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        """Return rebalance dates in ``market`` within the inclusive range."""
        raise NotImplementedError


class SignalSource(ABC):
    """Generates per-symbol signals on a decision date (SP 2.25–2.26).

    A signal is a numeric score where higher means more preferred. The source
    is point-in-time: it may only use data knowable on or before the decision
    date (SP 2.9). Normalization and direction are applied by the factor layer
    (SP 2.22) before selection.
    """

    @abstractmethod
    def signals(
        self,
        market: Market,
        decision_date: date,
        symbols: Sequence[str],
    ) -> Mapping[str, float]:
        """Return a signal score per symbol on the decision date."""
        raise NotImplementedError


class PortfolioBuilder(ABC):
    """Turns signals into target positions and order drafts (SP 2.34–2.36).

    The builder is market-scoped and uses only data knowable on the decision
    date. FX conversion to the benchmark currency is applied before orders are
    drafted (SP 2.36); order quantities are expressed in the security's own
    units.
    """

    @abstractmethod
    def target_positions(
        self,
        market: Market,
        decision_date: date,
        current: BacktestState,
        signals: Mapping[str, float],
    ) -> tuple[Position, ...]:
        """Return the desired end-of-rebalance positions."""
        raise NotImplementedError

    @abstractmethod
    def order_drafts(
        self,
        market: Market,
        decision_date: date,
        current: BacktestState,
        target_positions: Sequence[Position],
        quotes: Mapping[str, DailyQuote],
    ) -> tuple[Order, ...]:
        """Return buy/sell order drafts that move the portfolio to targets."""
        raise NotImplementedError


class FillSimulator(ABC):
    """Simulates order execution against market data (SP 2.39–2.41).

    The simulator decides the fill price (open/close/next open, SP 2.39),
    applies volume-participation limits (SP 2.40) and refuses to trade
    suspended or otherwise untradeable symbols (SP 2.41). Fills carry the fee
    computed by the configured cost model (SP 2.37 / 2.38), which the
    implementation applies.
    """

    @abstractmethod
    def simulate(
        self,
        market: Market,
        day: date,
        orders: Sequence[Order],
        quotes: Mapping[str, DailyQuote],
    ) -> tuple[Fill, ...]:
        """Return fills for the orders that can execute on ``day``."""
        raise NotImplementedError


@dataclass(frozen=True)
class BacktestReport:
    """Artifacts of a completed backtest run ready for reporting (SP 2.45)."""

    state: BacktestState
    net_values: tuple[NetValue, ...] = ()
    fills: tuple[Fill, ...] = ()


class BacktestReporter(ABC):
    """Renders research reports from a completed backtest (SP 2.32, 2.45)."""

    @abstractmethod
    def render(self, report: BacktestReport) -> str:
        """Render a human-readable report (markdown) for the run."""
        raise NotImplementedError
