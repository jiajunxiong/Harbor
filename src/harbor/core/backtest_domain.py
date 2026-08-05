"""Immutable domain types for the Harbor backtest engine (MVP 2 / SP 2.2).

These value types are the shared vocabulary for the backtest interfaces
(SP 2.3), the Pydantic run configuration (SP 2.4) and the execution
orchestration (SP 2.47). Every type is immutable: a frozen dataclass or an
enum. The running backtest is advanced by constructing new values, never by
editing fields in place, so any recorded state can be replayed
deterministically.

Money and quantity are ``float`` to stay consistent with the rest of Harbor
(``Numeric(20, 6)`` columns are read into ``float``). The MVP is long-only, so
cash and position quantities are validated as non-negative.

The :class:`Market` vocabulary deliberately mirrors ``harbor.config.MarketTarget``
but exposes only concrete trading markets (``HK`` / ``US``): a single order,
fill, position or trading day never belongs to the ``BOTH`` ingestion target.
Conversion helpers keep the two enums in sync; a test enforces that they do
not drift apart.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from harbor.config import MarketTarget


class Market(StrEnum):
    """A concrete trading market in the backtest domain."""

    HK = "HK"
    US = "US"


def to_market_target(market: Market) -> MarketTarget:
    """Map a backtest market to the global market target enum."""
    return MarketTarget(market.value)


def from_market_target(target: MarketTarget) -> Market:
    """Map a global market target to a backtest market.

    Raises:
        ValueError: If ``target`` is ``MarketTarget.BOTH``, since a backtest
            position, order or trading day always belongs to one concrete
            market.
    """
    if target is MarketTarget.BOTH:
        raise ValueError("A backtest market must be a single concrete market.")
    return Market(target.value)


class Currency(StrEnum):
    """A currency held by or traded within the backtest."""

    HKD = "HKD"
    USD = "USD"


class OrderSide(StrEnum):
    """Buy or sell direction of an order or fill."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """How an order is priced when submitted to the fill simulator."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"


class BacktestStatus(StrEnum):
    """Lifecycle status of a backtest run (see SP 2.46 state machine)."""

    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class TradingDay:
    """A single calendar day within one market, with trading flags.

    HK and US keep independent calendars, so a trading day is scoped to a
    market. The calendar interface (SP 2.11) produces these values; a
    rebalance day is always a trading day, since a rebalance that lands on a
    holiday is deferred to the next trading day (SP 2.33).
    """

    market: Market
    date: date
    is_trading_day: bool = True
    is_rebalance_day: bool = False

    def __post_init__(self) -> None:
        if self.is_rebalance_day and not self.is_trading_day:
            raise ValueError("A rebalance day must be a trading day.")


@dataclass(frozen=True)
class Order:
    """An immutable buy or sell order in the backtest.

    ``ref`` ties the order to the rebalance (or other decision) that produced
    it so every order is auditable; ``currency`` is the currency the security
    is quoted in. Fractional quantities are allowed because US shares may be
    traded fractionally (SP 2.38).
    """

    symbol: str
    market: Market
    side: OrderSide
    quantity: float
    currency: Currency
    trade_date: date
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    ref: str = ""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Order symbol must be non-empty.")
        if self.quantity <= 0:
            raise ValueError("Order quantity must be positive.")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("A limit order requires a limit price.")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("A market order cannot carry a limit price.")


@dataclass(frozen=True)
class Fill:
    """An immutable record of an executed order (成交).

    ``order_ref`` points at the originating :class:`Order`; fees from the cost
    model (SP 2.37 / 2.38) are captured here so every execution is auditable.
    """

    order_ref: str
    symbol: str
    market: Market
    side: OrderSide
    quantity: float
    price: float
    currency: Currency
    trade_date: date
    fee: float = 0.0

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Fill symbol must be non-empty.")
        if self.quantity <= 0:
            raise ValueError("Fill quantity must be positive.")
        if self.price < 0:
            raise ValueError("Fill price must be non-negative.")
        if self.fee < 0:
            raise ValueError("Fill fee must be non-negative.")

    @property
    def notional(self) -> float:
        """Gross trade value before fees."""
        return self.quantity * self.price


@dataclass(frozen=True)
class Position:
    """An immutable holding in a single security (持仓)."""

    symbol: str
    market: Market
    quantity: float
    average_cost: float
    currency: Currency
    as_of_date: date

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Position symbol must be non-empty.")
        if self.quantity < 0:
            raise ValueError("Position quantity must be non-negative.")
        if self.average_cost < 0:
            raise ValueError("Position average cost must be non-negative.")

    @property
    def cost_basis(self) -> float:
        """Total acquisition cost before fees."""
        return self.quantity * self.average_cost


@dataclass(frozen=True)
class CashBalance:
    """An immutable cash balance in a single currency.

    The MVP is long-only, so a negative balance is rejected; the multi-currency
    ledger (SP 2.42) decides which currency balances exist and how they are
    converted to the benchmark.
    """

    currency: Currency
    amount: float

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("Cash amount must be non-negative.")


@dataclass(frozen=True)
class NetValue:
    """An immutable net-value snapshot (净值) expressed in one currency.

    The MVP portfolio is long-only: cash and securities values are
    non-negative and total value is their sum. FX conversion to the benchmark
    currency is applied by the ledger and valuation layers (SP 2.42 / 2.45)
    before a snapshot is constructed.
    """

    as_of_date: date
    currency: Currency
    cash: float
    securities_value: float
    fees_paid: float = 0.0

    def __post_init__(self) -> None:
        if self.cash < 0:
            raise ValueError("Net value cash must be non-negative.")
        if self.securities_value < 0:
            raise ValueError("Net value securities must be non-negative.")
        if self.fees_paid < 0:
            raise ValueError("Net value fees must be non-negative.")

    @property
    def total_value(self) -> float:
        """Cash plus securities value."""
        return self.cash + self.securities_value


@dataclass(frozen=True)
class BacktestState:
    """An immutable snapshot of the running backtest (回测状态).

    The orchestration (SP 2.47) advances the run by producing new state
    values; the full trade and fill history is persisted to result tables
    (SP 2.7) rather than carried inside this snapshot. A failed run retains
    its diagnostic message (SP 2.46).
    """

    status: BacktestStatus
    as_of_date: date
    positions: tuple[Position, ...] = ()
    cash: tuple[CashBalance, ...] = ()
    pending_orders: tuple[Order, ...] = ()
    next_rebalance_date: date | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.status is BacktestStatus.FAILED and self.error_message is None:
            raise ValueError("A failed backtest must carry an error message.")
