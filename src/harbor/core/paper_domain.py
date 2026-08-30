"""Immutable paper-trading domain types (MVP 4 / SP 4.1).

These value types are the shared vocabulary for the paper-trading (模拟盘)
loop: the paper run master record, the account, positions, orders, fills,
signal intentions, risk approvals and circuit-breaker state. Every type is
immutable — a frozen dataclass or an enum — so recorded paper state can be
replayed deterministically and no later edit can silently change a recorded
order, fill or approval.

The :class:`PaperStatus` vocabulary drives the paper state machine (SP 4.10);
:class:`PaperOrderStatus` enumerates the order lifecycle (SP 4.6 / 4.20, the
legal-transition machine is enforced in stage 2). Markets, currencies and
order sides reuse the MVP 2 backtest domain (SP 2.2) so a paper order never
mixes HK/US rules. Pure core logic: depends only on the backtest domain and
never touches storage, services or CLI code.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from harbor.core.backtest_domain import Currency, Market, OrderSide


class PaperStatus(StrEnum):
    """Lifecycle status of a paper run (SP 4.10 state machine).

    A run starts as ``DRAFT``, must be ``APPROVED`` before it can become
    ``ACTIVE``. An active run may be ``PAUSED`` or trip a circuit breaker into
    ``CIRCUIT_BROKEN``; ``STOPPED`` is terminal.
    """

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CIRCUIT_BROKEN = "CIRCUIT_BROKEN"
    STOPPED = "STOPPED"


class PaperRunMode(StrEnum):
    """How a paper run advances (SP 4.2).

    ``MANUAL`` requires human approval for high-risk actions (SP 4.39);
    ``AUTO`` follows the configured risk rules automatically.
    """

    MANUAL = "MANUAL"
    AUTO = "AUTO"


class PaperOrderStatus(StrEnum):
    """Lifecycle status of a paper order (SP 4.6 / 4.20).

    The full legal-transition machine is enforced in stage 2 (SP 4.20); the
    status vocabulary is fixed here so orders persisted in stage 1 are
    forward-compatible.
    """

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PaperPriceType(StrEnum):
    """How a paper order is priced (SP 4.6 / 4.19)."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    REFERENCE = "REFERENCE"


class ApprovalDecision(StrEnum):
    """The outcome of a risk approval (SP 4.7 / 4.39)."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CircuitBreakerKind(StrEnum):
    """What kind of circuit breaker tripped (SP 4.7 / 4.36)."""

    DAILY = "DAILY"
    MONTHLY = "MONTHLY"
    DRAWDOWN = "DRAWDOWN"


def _require_utc_aware(timestamp: datetime, what: str) -> None:
    """Require an explicit UTC offset so timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError(f"{what} must be UTC-aware (offset 0).")


@dataclass(frozen=True)
class PaperRun:
    """A master record of one paper run (SP 4.1 / 4.5 / 4.9).

    Records the strategy identity/version, the config hash (SP 4.2), the
    dataset fingerprint and code version (SP 4.9 replay identity), the market
    scope, base currency and lifecycle status so every run is traceable and
    replayable.
    """

    run_id: str
    strategy: str
    strategy_version: str
    config_hash: str
    dataset_fingerprint: str
    code_version: str
    markets: tuple[Market, ...]
    base_currency: Currency
    status: PaperStatus
    created_at: datetime
    started_at: datetime | None = None
    stopped_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.run_id or not self.strategy or not self.strategy_version:
            raise ValueError("Paper run id, strategy and strategy version must be non-empty.")
        if not self.config_hash or not self.dataset_fingerprint or not self.code_version:
            raise ValueError(
                "Paper run config hash, dataset fingerprint and code version must be non-empty."
            )
        if not self.markets:
            raise ValueError("A paper run must span at least one market.")
        if len({market for market in self.markets}) != len(self.markets):
            raise ValueError("Paper run markets must be unique.")
        if (
            self.started_at is not None
            and self.stopped_at is not None
            and self.stopped_at < self.started_at
        ):
            raise ValueError("stopped_at must be on or after started_at.")

    def readable(self) -> str:
        """Render the run as a compact summary."""
        markets = ",".join(market.value for market in self.markets)
        return (
            f"paper run {self.run_id} {self.strategy}@{self.strategy_version} "
            f"{self.status.value} markets {markets} base {self.base_currency.value} "
            f"fp {self.dataset_fingerprint}"
        )


@dataclass(frozen=True)
class PaperAccount:
    """An immutable paper account (SP 4.1 / 4.4).

    ``base_currency`` is the accounting/benchmark currency; ``currencies``
    lists the ledger currencies the account may hold (HKD/USD/base). The
    multi-currency cash ledger and positions are managed by
    :mod:`harbor.core.paper_account` (SP 4.4).
    """

    account_id: str
    base_currency: Currency
    currencies: tuple[Currency, ...]
    initial_capital: float
    opened_at: date

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("Account id must be non-empty.")
        if not self.currencies:
            raise ValueError("An account must hold at least one currency.")
        if self.base_currency not in self.currencies:
            raise ValueError("An account's base currency must be in its ledger currencies.")
        if len({currency for currency in self.currencies}) != len(self.currencies):
            raise ValueError("Account currencies must be unique.")
        if self.initial_capital < 0:
            raise ValueError("Initial capital must be non-negative.")

    def readable(self) -> str:
        """Render the account as a compact summary."""
        currencies = ",".join(currency.value for currency in self.currencies)
        return (
            f"paper account {self.account_id} base {self.base_currency.value} "
            f"initial {self.initial_capital:.2f} currencies {currencies}"
        )


@dataclass(frozen=True)
class PaperPosition:
    """A holding in a paper account with its cost basis (SP 4.1 / 4.4)."""

    market: Market
    symbol: str
    quantity: float
    average_cost: float
    currency: Currency
    acquired_at: date

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

    def readable(self) -> str:
        """Render the position as a compact summary."""
        return (
            f"{self.market.value}/{self.symbol} qty {self.quantity:g} "
            f"avg {self.average_cost:g} {self.currency.value}"
        )


@dataclass(frozen=True)
class SignalIntention:
    """A normalized strategy signal (SP 4.1 / 4.14).

    ``target_weights`` maps symbol -> target weight within the market.
    ``source_run_id`` links the signal to the MVP 2/3 research run that
    produced it (SP 4.25 traceability).
    """

    intention_id: str
    paper_run_id: str
    strategy: str
    strategy_version: str
    market: Market
    rebalance_date: date
    target_weights: tuple[tuple[str, float], ...]
    created_at: datetime
    source_run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.intention_id or not self.paper_run_id:
            raise ValueError("Intention id and paper run id must be non-empty.")
        if not self.strategy or not self.strategy_version:
            raise ValueError("Strategy and strategy version must be non-empty.")
        symbols = [symbol for symbol, _weight in self.target_weights]
        if len(symbols) != len(set(symbols)):
            raise ValueError("Target weights must not contain duplicate symbols.")
        for _symbol, weight in self.target_weights:
            if weight < 0:
                raise ValueError("Target weights must be non-negative.")

    def weight_of(self, symbol: str) -> float | None:
        """Return the target weight for ``symbol`` (None when absent)."""
        for sym, weight in self.target_weights:
            if sym == symbol:
                return weight
        return None

    def readable(self) -> str:
        """Render the signal as a compact summary."""
        weights = ", ".join(f"{symbol}:{weight:g}" for symbol, weight in self.target_weights)
        source = "" if self.source_run_id is None else f" source {self.source_run_id}"
        return (
            f"signal {self.intention_id} run {self.paper_run_id} "
            f"{self.market.value} on {self.rebalance_date.isoformat()} "
            f"weights {{{weights}}}{source}"
        )


@dataclass(frozen=True)
class PaperOrder:
    """An immutable paper order (SP 4.1 / 4.6).

    ``intention_id`` links the order to its signal intention (SP 4.25);
    ``ref`` is a free-form audit reference. Fractional quantities are allowed
    because US shares may be traded fractionally (SP 2.38).
    """

    order_id: str
    paper_run_id: str
    market: Market
    symbol: str
    side: OrderSide
    quantity: float
    currency: Currency
    price_type: PaperPriceType
    status: PaperOrderStatus
    created_at: datetime
    intention_id: str | None = None
    ref: str = ""

    def __post_init__(self) -> None:
        if not self.order_id or not self.paper_run_id:
            raise ValueError("Order id and paper run id must be non-empty.")
        if not self.symbol:
            raise ValueError("Order symbol must be non-empty.")
        if self.quantity <= 0:
            raise ValueError("Order quantity must be positive.")

    def readable(self) -> str:
        """Render the order as a compact summary."""
        return (
            f"order {self.order_id} {self.side.value} {self.market.value}/{self.symbol} "
            f"qty {self.quantity:g} {self.currency.value} {self.price_type.value} "
            f"{self.status.value}"
        )


@dataclass(frozen=True)
class PaperFill:
    """An executed paper order (成交, SP 4.1 / 4.6)."""

    fill_id: str
    paper_order_id: str
    paper_run_id: str
    market: Market
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    currency: Currency
    trade_date: date
    fee: float = 0.0

    def __post_init__(self) -> None:
        if not self.fill_id or not self.paper_order_id or not self.paper_run_id:
            raise ValueError("Fill id, order id and paper run id must be non-empty.")
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

    def readable(self) -> str:
        """Render the fill as a compact summary."""
        return (
            f"fill {self.fill_id} {self.side.value} {self.market.value}/{self.symbol} "
            f"qty {self.quantity:g} @ {self.price:g} {self.currency.value} on "
            f"{self.trade_date.isoformat()}"
        )


@dataclass(frozen=True)
class RiskApproval:
    """A recorded human/system risk approval (SP 4.1 / 4.7 / 4.39).

    ``scope`` names what was approved (e.g. ``order:order-1`` or ``run:run-1``);
    ``rule`` names the risk rule/limit. The approver, decision, reason and
    timestamp make every manual intervention auditable.
    """

    approval_id: str
    paper_run_id: str
    scope: str
    approver: str
    decision: ApprovalDecision
    rule: str
    decided_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.approval_id or not self.paper_run_id:
            raise ValueError("Approval id and paper run id must be non-empty.")
        if not self.scope or not self.approver or not self.rule:
            raise ValueError("Approval scope, approver and rule must be non-empty.")
        _require_utc_aware(self.decided_at, "Approval decision time")

    def readable(self) -> str:
        """Render the approval as a compact summary."""
        reason = f" ({self.reason})" if self.reason is not None else ""
        return (
            f"approval {self.approval_id} {self.decision.value} {self.scope} "
            f"by {self.approver} rule {self.rule}{reason} at "
            f"{self.decided_at.isoformat()}"
        )


@dataclass(frozen=True)
class CircuitBreakerState:
    """The frozen/unfrozen state of a paper circuit breaker (SP 4.1 / 4.7).

    ``scope`` names what the breaker freezes (e.g. ``new_orders`` or
    ``market:HK``); ``triggered`` is True while frozen and False once the
    breaker has been recovered (SP 4.42).
    """

    breaker_id: str
    paper_run_id: str
    kind: CircuitBreakerKind
    triggered: bool
    scope: str
    reason: str
    frozen_at: datetime
    recovered_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.breaker_id or not self.paper_run_id:
            raise ValueError("Breaker id and paper run id must be non-empty.")
        if not self.scope or not self.reason:
            raise ValueError("Breaker scope and reason must be non-empty.")
        _require_utc_aware(self.frozen_at, "Breaker frozen time")
        if self.recovered_at is not None:
            _require_utc_aware(self.recovered_at, "Breaker recovery time")
            if self.recovered_at < self.frozen_at:
                raise ValueError("recovered_at must be on or after frozen_at.")
        if self.triggered and self.recovered_at is not None:
            raise ValueError("A triggered breaker cannot already be recovered.")
        if not self.triggered and self.recovered_at is None:
            raise ValueError("A recovered breaker must record a recovery time.")

    def readable(self) -> str:
        """Render the breaker as a compact summary."""
        state = "TRIGGERED" if self.triggered else "RECOVERED"
        recovery = (
            "" if self.recovered_at is None else f" recovered {self.recovered_at.isoformat()}"
        )
        return (
            f"breaker {self.breaker_id} {self.kind.value} {state} scope {self.scope} "
            f"reason {self.reason} frozen {self.frozen_at.isoformat()}{recovery}"
        )
