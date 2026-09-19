"""Paper run, signal, order, approval, reconcile and report service (MVP 4 / SP 4.84-4.87).

Orchestrates the ``harbor-cli paper`` commands over the paper core and storage
modules:

* ``init`` creates a DRAFT paper run master record from a versioned paper
  configuration (SP 4.84), recording the config hash, dataset fingerprint,
  code version, market scope and base currency (SP 4.5 / 4.9).
* ``start`` / ``stop`` advance the SP 4.10 state machine (DRAFT -> APPROVED ->
  ACTIVE and to STOPPED), recording the approval (SP 4.7 / 4.39) so the
  transition is auditable.
* ``signal`` derives and persists paper orders from target weights through the
  SP 4.14 -> 4.16 pipeline (signal -> target portfolio -> order drafts),
  aligned by the SP 4.17/4.18 market order rules (SP 4.85).
* ``order list/show`` and ``approve``/``reject`` surface orders and record the
  approval (SP 4.86); ``reconcile`` rebuilds the account from persisted fills
  and records every difference (SP 4.58 / 4.61) — never silently corrected.
* ``report`` renders a run's status, orders and differences as JSON / CSV /
  HTML (SP 4.87).

The service depends on a :class:`PaperStore` abstraction so the orchestration
is pure and testable with an in-memory store; the CLI wires
:class:`PaperRepositoryStore` over a database connection (SP 4.5-4.8).
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Protocol

from sqlalchemy.engine import Connection

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_account import (
    account_assets_base,
    apply_paper_fill,
    open_paper_account,
)
from harbor.core.paper_config_loader import config_hash, load_paper_config
from harbor.core.paper_domain import (
    ApprovalDecision,
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
    PaperStatus,
    RiskApproval,
)
from harbor.core.paper_order_drafts import derive_order_drafts
from harbor.core.paper_order_rules import apply_paper_order_rule
from harbor.core.paper_reconciliation import (
    PaperReconciliationDifference,
    PaperReconciliationResult,
    reconciliation_rows,
)
from harbor.core.paper_signal import build_signal_intention
from harbor.core.paper_state_machine import PaperRunState, PaperStateError
from harbor.core.paper_target_portfolio import derive_target_portfolio
from harbor.storage.paper_repositories import PaperRepository

#: Page bounds for the run list. Deliberately equal to the read API's (SP 5.6) so
#: that both surfaces answer "how many paper runs are there?" the same way; a
#: test pins the three constants together so they cannot drift apart.
DEFAULT_RUN_LIST_LIMIT = 50
MAX_RUN_LIST_LIMIT = 200


class PaperServiceError(ValueError):
    """Raised when a paper command cannot be orchestrated (SP 4.84)."""


@dataclass(frozen=True)
class PaperCommandResult:
    """The outcome of a paper lifecycle command (SP 4.84)."""

    run_id: str
    status: str
    message: str = ""

    def to_dict(self) -> dict[str, str]:
        """Render the result as a JSON-serializable summary."""
        return {"run_id": self.run_id, "status": self.status, "message": self.message}


@dataclass(frozen=True)
class PaperSignalResult:
    """The outcome of deriving orders from a signal (SP 4.85)."""

    run_id: str
    order_count: int
    intention_ids: tuple[str, ...]
    skipped: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Render the result as a JSON-serializable summary."""
        return {
            "run_id": self.run_id,
            "order_count": self.order_count,
            "intention_ids": list(self.intention_ids),
            "skipped": list(self.skipped),
        }


@dataclass(frozen=True)
class PaperReconcileResult:
    """The outcome of reconciling a paper run (SP 4.58 / 4.61)."""

    run_id: str
    as_of: date
    difference_count: int
    reconciled: bool

    def to_dict(self) -> dict[str, object]:
        """Render the result as a JSON-serializable summary."""
        return {
            "run_id": self.run_id,
            "as_of": self.as_of.isoformat(),
            "difference_count": self.difference_count,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True)
class PaperRunListResult:
    """One bounded page of paper runs, newest first (audit lookup).

    ``total`` is the full count *before* pagination and ``next_offset`` is
    ``None`` only on the last page, so a caller can tell a complete answer from
    a truncated one instead of assuming it saw every run.
    """

    runs: tuple[dict[str, object], ...]
    total: int
    limit: int
    offset: int
    next_offset: int | None

    def to_dict(self) -> dict[str, object]:
        """Render the page as a JSON-serializable summary."""
        return {
            "runs": list(self.runs),
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
            "next_offset": self.next_offset,
        }


class PaperStore(Protocol):
    """The storage surface the paper service needs (SP 4.5-4.8)."""

    def create_run(
        self,
        *,
        run_id: str,
        strategy: str,
        strategy_version: str,
        config_hash: str,
        config_snapshot: Mapping[str, object],
        dataset_fingerprint: str,
        code_version: str,
        markets: Sequence[str],
        base_currency: str,
        created_at: datetime,
        status: str,
    ) -> int: ...

    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def list_runs(self, *, limit: int, offset: int) -> Sequence[dict[str, Any]]: ...

    def count_runs(self) -> int: ...

    def update_run(
        self,
        *,
        run_id: str,
        status: str,
        started_at: datetime | None = None,
        stopped_at: datetime | None = None,
    ) -> int: ...

    def insert_approval(self, paper_run_id: str, approval: RiskApproval) -> int: ...

    def list_approvals(self, paper_run_id: str) -> Sequence[dict[str, Any]]: ...

    def insert_orders(self, paper_run_id: str, orders: Sequence[PaperOrder]) -> int: ...

    def list_orders(self, paper_run_id: str) -> Sequence[dict[str, Any]]: ...

    def insert_fills(self, paper_run_id: str, fills: Sequence[PaperFill]) -> int: ...

    def list_fills(self, paper_run_id: str) -> Sequence[dict[str, Any]]: ...

    def insert_net_values(self, paper_run_id: str, rows: Sequence[Mapping[str, object]]) -> int: ...

    def list_net_values(self, paper_run_id: str) -> Sequence[dict[str, Any]]: ...

    def insert_reconciliation_differences(
        self, paper_run_id: str, rows: Sequence[Mapping[str, object]]
    ) -> int: ...

    def list_reconciliation_differences(self, paper_run_id: str) -> Sequence[dict[str, Any]]: ...


class PaperRepositoryStore:
    """A :class:`PaperStore` adapter over the storage repository (SP 4.5-4.8)."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._repository = PaperRepository(connection)

    def create_run(self, **kwargs: object) -> int:
        return self._repository.create_run(**kwargs)  # type: ignore[arg-type]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = [
            dict(row)
            for row in self._connection.execute(self._repository.get_run(run_id)).mappings()
        ]
        return rows[0] if rows else None

    def list_runs(self, *, limit: int, offset: int) -> Sequence[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                self._repository.list_runs(limit=limit, offset=offset)
            ).mappings()
        ]

    def count_runs(self) -> int:
        return int(self._connection.execute(self._repository.count_runs()).scalar_one())

    def update_run(self, **kwargs: object) -> int:
        return self._repository.update_run(**kwargs)  # type: ignore[arg-type]

    def insert_approval(self, paper_run_id: str, approval: RiskApproval) -> int:
        return self._repository.insert_approval(paper_run_id, approval)

    def list_approvals(self, paper_run_id: str) -> Sequence[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                self._repository.list_approvals(paper_run_id)
            ).mappings()
        ]

    def insert_orders(self, paper_run_id: str, orders: Sequence[PaperOrder]) -> int:
        return self._repository.insert_orders(paper_run_id, orders)

    def list_orders(self, paper_run_id: str) -> Sequence[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                self._repository.list_orders(paper_run_id)
            ).mappings()
        ]

    def insert_fills(self, paper_run_id: str, fills: Sequence[PaperFill]) -> int:
        return self._repository.insert_fills(paper_run_id, fills)

    def list_fills(self, paper_run_id: str) -> Sequence[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                self._repository.list_fills(paper_run_id)
            ).mappings()
        ]

    def insert_net_values(self, paper_run_id: str, rows: Sequence[Mapping[str, object]]) -> int:
        return self._repository.insert_net_values(paper_run_id, rows)

    def list_net_values(self, paper_run_id: str) -> Sequence[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                self._repository.list_net_values(paper_run_id)
            ).mappings()
        ]

    def insert_reconciliation_differences(
        self, paper_run_id: str, rows: Sequence[Mapping[str, object]]
    ) -> int:
        return self._repository.insert_reconciliation_differences(paper_run_id, rows)

    def list_reconciliation_differences(self, paper_run_id: str) -> Sequence[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                self._repository.list_reconciliation_differences(paper_run_id)
            ).mappings()
        ]


def _now_utc() -> datetime:
    """Return the current UTC time (paper command timestamp)."""
    return datetime.now(timezone.utc)


def _require_run(store: PaperStore, run_id: str) -> dict[str, Any]:
    """Return a run row or raise when it does not exist (SP 4.84)."""
    row = store.get_run(run_id)
    if row is None:
        raise PaperServiceError(f"Paper run {run_id!r} does not exist.")
    return row


def _state_from_row(row: dict[str, Any]) -> PaperRunState:
    """Build a paper run state from a persisted run row (SP 4.10).

    The persisted status is authoritative; the transition trail is not
    persisted, so a fresh state is reconstructed at the current status (the
    audit trail lives in the risk_approvals / audit events).
    """
    return PaperRunState(run_id=str(row["run_id"]), status=PaperStatus(str(row["status"])))


def _quote_currency(market: Market) -> Currency:
    """Return the currency securities in ``market`` are quoted in (SP 4.4)."""
    return Currency.HKD if market is Market.HK else Currency.USD


def _market_of(symbol: str) -> Market:
    """Infer a symbol's market from its suffix (``.HK`` / ``.US``)."""
    if symbol.endswith(".HK"):
        return Market.HK
    if symbol.endswith(".US"):
        return Market.US
    raise PaperServiceError(f"Cannot infer market for symbol {symbol!r}; use a .HK or .US suffix.")


def paper_init_command(
    *,
    config_path: str,
    store: PaperStore,
    code_version: str,
    dataset_fingerprint: str,
    created_at: datetime | None = None,
) -> PaperCommandResult:
    """Create a DRAFT paper run from a versioned config file (SP 4.84).

    Loads and validates the SP 4.2 paper config, computes its stable hash
    (SP 4.2 / 4.9), assigns a new run id and persists the master record via the
    SP 4.5 repository. The run id is usable by the later ``start`` / ``stop`` /
    ``status`` / ``signal`` / ``order`` / ``approve`` / ``reconcile`` /
    ``report`` commands.

    Raises:
        PaperServiceError: If the configuration cannot be loaded.
    """
    try:
        config = load_paper_config(config_path)
    except (OSError, ValueError) as error:
        raise PaperServiceError(f"Cannot load paper config: {error}.") from error
    run_id = uuid.uuid4().hex
    store.create_run(
        run_id=run_id,
        strategy=config.strategy,
        strategy_version=config.strategy_version,
        config_hash=config_hash(config),
        config_snapshot=config.model_dump(mode="json"),
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        markets=[market.value for market in config.markets],
        base_currency=config.base_currency.value,
        created_at=created_at if created_at is not None else _now_utc(),
        status=PaperStatus.DRAFT.value,
    )
    return PaperCommandResult(
        run_id=run_id,
        status=PaperStatus.DRAFT.value,
        message="paper run created in DRAFT; approve and start to activate",
    )


def _record_run_approval(
    store: PaperStore,
    run_id: str,
    *,
    approver: str,
    decision: ApprovalDecision,
    rule: str,
    reason: str | None,
    decided_at: datetime,
) -> None:
    """Record a risk approval for the run (SP 4.7 / 4.39)."""
    approval = RiskApproval(
        approval_id=uuid.uuid4().hex,
        paper_run_id=run_id,
        scope="run",
        approver=approver,
        decision=decision,
        rule=rule,
        decided_at=decided_at,
        reason=reason,
    )
    store.insert_approval(run_id, approval)


def paper_start_command(
    *,
    store: PaperStore,
    run_id: str,
    approver: str = "system",
    reason: str = "run approved and activated",
    recorded_at: datetime | None = None,
) -> PaperCommandResult:
    """Approve and activate a paper run (DRAFT -> APPROVED -> ACTIVE, SP 4.84).

    A run that is already APPROVED is only activated; the approval is recorded
    once (SP 4.7 / 4.39) so the transition is auditable.

    Raises:
        PaperServiceError: If the run does not exist or the transition is not
            legal (propagated from SP 4.10).
    """
    row = _require_run(store, run_id)
    state = _state_from_row(row)
    timestamp = recorded_at if recorded_at is not None else _now_utc()
    if state.status is PaperStatus.DRAFT:
        state = state.approve(reason=reason, recorded_at=timestamp)
        _record_run_approval(
            store,
            run_id,
            approver=approver,
            decision=ApprovalDecision.APPROVED,
            rule="paper_start",
            reason=reason,
            decided_at=timestamp,
        )
    if state.status is PaperStatus.APPROVED:
        state = state.activate(reason=reason, recorded_at=timestamp)
    store.update_run(run_id=run_id, status=state.status.value, started_at=timestamp)
    return PaperCommandResult(
        run_id=run_id,
        status=state.status.value,
        message="paper run approved and activated",
    )


def paper_stop_command(
    *,
    store: PaperStore,
    run_id: str,
    reason: str = "run stopped",
    recorded_at: datetime | None = None,
) -> PaperCommandResult:
    """Stop a paper run (terminal state, SP 4.84)."""
    row = _require_run(store, run_id)
    state = _state_from_row(row)
    timestamp = recorded_at if recorded_at is not None else _now_utc()
    try:
        state = state.stop(reason=reason, recorded_at=timestamp)
    except PaperStateError as error:
        raise PaperServiceError(f"Cannot stop paper run: {error}.") from error
    store.update_run(run_id=run_id, status=state.status.value, stopped_at=timestamp)
    return PaperCommandResult(run_id=run_id, status=state.status.value, message="paper run stopped")


def paper_status_command(*, store: PaperStore, run_id: str) -> dict[str, object]:
    """Render a paper run's status view (SP 4.84 / 4.87)."""
    row = _require_run(store, run_id)
    return {
        "run_id": row["run_id"],
        "strategy": row["strategy"],
        "strategy_version": row["strategy_version"],
        "status": row["status"],
        "config_hash": row["config_hash"],
        "dataset_fingerprint": row["dataset_fingerprint"],
        "code_version": row["code_version"],
        "markets": row["markets"],
        "base_currency": row["base_currency"],
        "created_at": _iso(row.get("created_at")),
        "started_at": _iso(row.get("started_at")),
        "stopped_at": _iso(row.get("stopped_at")),
        "order_count": len(store.list_orders(run_id)),
        "fill_count": len(store.list_fills(run_id)),
        "approval_count": len(store.list_approvals(run_id)),
        "difference_count": len(store.list_reconciliation_differences(run_id)),
    }


def _iso(value: object) -> str | None:
    """Render a timestamp as ISO or ``None`` when absent."""
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return str(iso())
    return str(value)


def _run_list_row(row: Mapping[str, Any]) -> dict[str, object]:
    """Render one run as the auditable identity the list exposes."""
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "strategy": row["strategy"],
        "strategy_version": row["strategy_version"],
        "code_version": row["code_version"],
        "markets": row["markets"],
        "base_currency": row["base_currency"],
        "dataset_fingerprint": row["dataset_fingerprint"],
        "created_at": _iso(row.get("created_at")),
        "started_at": _iso(row.get("started_at")),
        "stopped_at": _iso(row.get("stopped_at")),
    }


def paper_list_command(
    *,
    store: PaperStore,
    limit: int | None = None,
    offset: int = 0,
) -> PaperRunListResult:
    """Return one bounded page of paper runs, newest first (audit lookup).

    ``docs/paper_examples.md`` recorded that the CLI offered no way to recover a
    run id once the printed value was lost, and told the operator to query the
    ``paper_runs`` table by hand. This command closes that gap by exposing the
    same read the monitoring API already performs (SP 5.8) under the same
    bounded page policy (SP 5.6), so the two surfaces agree.

    Raises:
        PaperServiceError: If ``limit`` is not a positive integer within the
            bound, or ``offset`` is negative. An unbounded query is refused
            rather than silently served, because an unbounded list is exactly
            the failure this command exists to prevent.
    """
    resolved_limit = DEFAULT_RUN_LIST_LIMIT if limit is None else limit
    if resolved_limit < 1:
        raise PaperServiceError(
            "limit must be a positive integer; unbounded queries are not permitted."
        )
    if resolved_limit > MAX_RUN_LIST_LIMIT:
        raise PaperServiceError(f"limit must not exceed {MAX_RUN_LIST_LIMIT}.")
    if offset < 0:
        raise PaperServiceError("offset must be a non-negative integer.")

    rows = store.list_runs(limit=resolved_limit, offset=offset)
    total = store.count_runs()
    runs = tuple(_run_list_row(row) for row in rows)
    consumed = min(offset + len(runs), total)
    return PaperRunListResult(
        runs=runs,
        total=total,
        limit=resolved_limit,
        offset=offset,
        next_offset=consumed if consumed < total else None,
    )


def paper_signal_command(
    *,
    store: PaperStore,
    run_id: str,
    rebalance_date: date,
    targets: Mapping[str, float],
    prices: Mapping[str, float],
    source_run_id: str | None = None,
    created_at: datetime | None = None,
) -> PaperSignalResult:
    """Derive and persist paper orders from target weights (SP 4.85).

    Groups the targets by market (inferred from the symbol suffix), runs the
    SP 4.14 -> 4.16 pipeline (signal -> target portfolio -> order drafts) and
    aligns every draft by the SP 4.17/4.18 market order rule before persisting
    the CREATED orders (SP 4.6). Drafts rejected by the order rule (e.g. below
    one HK lot) are skipped with their symbol recorded — never silently
    dropped (SP 4.21).

    Raises:
        PaperServiceError: If the run does not exist, a target has no price, or
            the signal is invalid (propagated from SP 4.14-4.16).
    """
    row = _require_run(store, run_id)
    state = _state_from_row(row)
    if state.status is PaperStatus.STOPPED:
        raise PaperServiceError(f"Paper run {run_id!r} is stopped; cannot derive orders.")
    snapshot = row["config_snapshot"]
    if not isinstance(snapshot, Mapping):
        raise PaperServiceError(f"Paper run {run_id!r} has no config snapshot.")
    base_currency = Currency(str(snapshot.get("base_currency", "HKD")))
    initial_capital = float(snapshot.get("initial_capital", 0.0))
    if initial_capital <= 0:
        raise PaperServiceError(f"Paper run {run_id!r} has a non-positive initial capital.")
    strategy = str(row["strategy"])
    strategy_version = str(row["strategy_version"])
    timestamp = created_at if created_at is not None else _now_utc()

    by_market: dict[Market, list[tuple[str, float]]] = {}
    for symbol, weight in targets.items():
        by_market.setdefault(_market_of(symbol), []).append((symbol, weight))

    orders: list[PaperOrder] = []
    intention_ids: list[str] = []
    skipped: list[str] = []
    for market, weights in by_market.items():
        intention_id = uuid.uuid4().hex
        intention_ids.append(intention_id)
        signal = build_signal_intention(
            intention_id=intention_id,
            paper_run_id=run_id,
            strategy=strategy,
            strategy_version=strategy_version,
            market=market,
            rebalance_date=rebalance_date,
            target_weights=tuple(weights),
            source_run_id=source_run_id,
            created_at=timestamp,
        )
        market_prices = {
            (market, symbol): prices[symbol] for symbol, _weight in weights if symbol in prices
        }
        missing = [symbol for symbol, _w in weights if symbol not in prices]
        if missing:
            raise PaperServiceError(
                f"Missing price for {', '.join(missing)} on {rebalance_date.isoformat()}."
            )
        target = derive_target_portfolio(
            signal=signal,
            positions={},
            prices=market_prices,
            portfolio_value=initial_capital,
            available_cash=initial_capital,
            base_currency=base_currency,
            fx_rate=lambda _from, _to, _day: 1.0 if _to is base_currency else None,
        )
        drafts = derive_order_drafts(
            target=target,
            positions={},
            available_cash=initial_capital,
            price_type=PaperPriceType.REFERENCE,
        )
        for draft in drafts.drafts:
            outcome = apply_paper_order_rule(draft.market, draft.quantity)
            if not outcome.ok:
                skipped.append(f"{draft.market.value}/{draft.symbol}")
                continue
            orders.append(
                PaperOrder(
                    order_id=uuid.uuid4().hex,
                    paper_run_id=run_id,
                    market=draft.market,
                    symbol=draft.symbol,
                    side=draft.side,
                    quantity=outcome.quantity,
                    currency=draft.currency,
                    price_type=draft.price_type,
                    status=PaperOrderStatus.CREATED,
                    created_at=timestamp,
                    intention_id=intention_id,
                )
            )
    store.insert_orders(run_id, orders)
    return PaperSignalResult(
        run_id=run_id,
        order_count=len(orders),
        intention_ids=tuple(intention_ids),
        skipped=tuple(skipped),
    )


def paper_order_list_command(*, store: PaperStore, run_id: str) -> list[dict[str, object]]:
    """List a run's orders (SP 4.85)."""
    _require_run(store, run_id)
    return [
        {
            "order_id": row["order_id"],
            "market": row["market"],
            "symbol": row["symbol"],
            "side": row["side"],
            "quantity": float(row["quantity"]),
            "currency": row["currency"],
            "price_type": row["price_type"],
            "status": row["status"],
            "intention_id": row.get("intention_id"),
        }
        for row in store.list_orders(run_id)
    ]


def paper_order_show_command(*, store: PaperStore, run_id: str, order_id: str) -> dict[str, object]:
    """Show a single order (SP 4.85)."""
    _require_run(store, run_id)
    for row in store.list_orders(run_id):
        if row["order_id"] == order_id:
            return {
                "order_id": row["order_id"],
                "paper_run_id": row["paper_run_id"],
                "market": row["market"],
                "symbol": row["symbol"],
                "side": row["side"],
                "quantity": float(row["quantity"]),
                "currency": row["currency"],
                "price_type": row["price_type"],
                "status": row["status"],
                "created_at": _iso(row.get("created_at")),
                "intention_id": row.get("intention_id"),
                "ref": row.get("ref"),
            }
    raise PaperServiceError(f"Order {order_id!r} does not exist in paper run {run_id!r}.")


def paper_approve_order_command(
    *,
    store: PaperStore,
    run_id: str,
    order_id: str,
    approver: str,
    decision: ApprovalDecision | str,
    reason: str | None = None,
    decided_at: datetime | None = None,
) -> PaperCommandResult:
    """Record an approval or rejection for an order (SP 4.86)."""
    _require_run(store, run_id)
    decision_enum = (
        decision if isinstance(decision, ApprovalDecision) else ApprovalDecision(decision)
    )
    order = paper_order_show_command(store=store, run_id=run_id, order_id=order_id)
    approval = RiskApproval(
        approval_id=uuid.uuid4().hex,
        paper_run_id=run_id,
        scope=f"order:{order_id}",
        approver=approver,
        decision=decision_enum,
        rule=f"order:{order.get('market')}/{order.get('symbol')}",
        decided_at=decided_at if decided_at is not None else _now_utc(),
        reason=reason,
    )
    store.insert_approval(run_id, approval)
    return PaperCommandResult(
        run_id=run_id,
        status=decision_enum.value,
        message=f"order {order_id} {decision_enum.value.lower()}",
    )


def _fill_from_row(row: dict[str, Any]) -> PaperFill:
    """Map a persisted fill row back to the domain (SP 4.6)."""
    return PaperFill(
        fill_id=str(row["fill_id"]),
        paper_order_id=str(row["order_id"]),
        paper_run_id=str(row["paper_run_id"]),
        market=Market(str(row["market"])),
        symbol=str(row["symbol"]),
        side=OrderSide(str(row["side"])),
        quantity=float(row["quantity"]),
        price=float(row["price"]),
        currency=Currency(str(row["currency"])),
        trade_date=_as_date(row["trade_date"]),
        fee=float(row["fee"]),
    )


def _as_date(value: object) -> date:
    """Return a date from a date or datetime value."""
    if isinstance(value, datetime):
        return value.date()
    return value  # type: ignore[return-value]


def paper_reconcile_command(
    *,
    store: PaperStore,
    run_id: str,
    as_of: date,
    tolerance: float = 1e-6,
) -> PaperReconcileResult:
    """Reconcile a paper run's account against its net-value snapshot (SP 4.87).

    Rebuilds the account from the persisted fills (SP 4.4 / 4.53), aggregates
    the assets in the base currency with explicit FX (SP 4.4) and compares
    them against the persisted daily net value (SP 4.8). Every divergence is
    recorded as a reconciliation difference (SP 4.61) — never silently
    corrected.
    """
    row = _require_run(store, run_id)
    snapshot = row["config_snapshot"]
    base_currency = Currency(str(snapshot.get("base_currency", "HKD")))
    initial_capital = float(snapshot.get("initial_capital", 0.0))
    opened_at = _as_date(row["created_at"]) if row.get("created_at") else as_of

    fills = sorted(
        (_fill_from_row(entry) for entry in store.list_fills(run_id)),
        key=lambda fill: fill.trade_date,
    )
    account = open_paper_account(
        account_id=f"paper-{run_id}",
        base_currency=base_currency,
        currencies=(base_currency,),
        initial_capital=initial_capital,
        opened_at=opened_at,
    )
    for fill in fills:
        account = apply_paper_fill(account, fill=fill)
    assets = account_assets_base(
        account,
        fx_rate=lambda _from, _to: 1.0 if _to is base_currency else None,
    )

    differences = _compare_snapshot(
        store,
        run_id,
        as_of,
        base_currency,
        assets.cash_base,
        assets.positions_base,
        tolerance,
    )
    store.insert_reconciliation_differences(
        run_id,
        reconciliation_rows(
            PaperReconciliationResult(as_of=as_of, differences=tuple(differences)),
            paper_run_id=run_id,
        ),
    )
    return PaperReconcileResult(
        run_id=run_id,
        as_of=as_of,
        difference_count=len(differences),
        reconciled=not differences,
    )


def _compare_snapshot(
    store: PaperStore,
    run_id: str,
    as_of: date,
    base_currency: Currency,
    cash_base: float,
    securities_base: float,
    tolerance: float,
) -> list[PaperReconciliationDifference]:
    """Compare the rebuilt assets against the persisted net-value snapshot.

    Returns the recorded differences (never silently corrected, SP 4.61).
    """
    snapshots = [
        row
        for row in store.list_net_values(run_id)
        if str(row.get("currency", "")) == base_currency.value
        and _as_date(row["as_of_date"]) <= as_of
    ]
    differences: list[PaperReconciliationDifference] = []
    if not snapshots:
        return [
            PaperReconciliationDifference(
                check_name="assets_close",
                expected=cash_base + securities_base,
                actual=0.0,
                detail=f"no net-value snapshot on or before {as_of.isoformat()}",
            )
        ]
    latest = snapshots[-1]
    expected_total = cash_base + securities_base
    actual_total = float(latest["total_value"])
    if abs(expected_total - actual_total) > tolerance:
        differences.append(
            PaperReconciliationDifference(
                check_name="assets_close",
                expected=expected_total,
                actual=actual_total,
                detail=(
                    f"rebuilt account {expected_total:.6f} vs snapshot "
                    f"{actual_total:.6f} on {as_of.isoformat()}"
                ),
            )
        )
    if abs(cash_base - float(latest["cash"])) > tolerance:
        differences.append(
            PaperReconciliationDifference(
                check_name="cash_close",
                expected=cash_base,
                actual=float(latest["cash"]),
                detail=f"rebuilt cash {cash_base:.6f} vs snapshot {float(latest['cash']):.6f}",
            )
        )
    if abs(securities_base - float(latest["securities_value"])) > tolerance:
        differences.append(
            PaperReconciliationDifference(
                check_name="securities_close",
                expected=securities_base,
                actual=float(latest["securities_value"]),
                detail=(
                    f"rebuilt securities {securities_base:.6f} vs snapshot "
                    f"{float(latest['securities_value']):.6f}"
                ),
            )
        )
    return differences


def _report_payload(store: PaperStore, run_id: str) -> dict[str, object]:
    """Assemble the paper run report payload (SP 4.87)."""
    status = paper_status_command(store=store, run_id=run_id)
    approvals = [
        {
            "approval_id": row["approval_id"],
            "scope": row["scope"],
            "approver": row["approver"],
            "decision": row["decision"],
            "rule": row["rule"],
            "decided_at": _iso(row.get("decided_at")),
            "reason": row.get("reason"),
        }
        for row in store.list_approvals(run_id)
    ]
    differences = [
        {
            "as_of_date": _iso(row.get("as_of_date")),
            "check_name": row["check_name"],
            "expected": float(row["expected"]),
            "actual": float(row["actual"]),
            "detail": row.get("detail"),
        }
        for row in store.list_reconciliation_differences(run_id)
    ]
    return {
        "status": status,
        "orders": paper_order_list_command(store=store, run_id=run_id),
        "approvals": approvals,
        "differences": differences,
    }


def _orders_csv(orders: Sequence[dict[str, object]]) -> str:
    """Render orders as CSV with a fixed header (SP 4.87)."""
    import csv as _csv
    import io as _io

    buffer = _io.StringIO()
    writer = _csv.writer(buffer, lineterminator="\n")
    fields = (
        "order_id",
        "market",
        "symbol",
        "side",
        "quantity",
        "currency",
        "price_type",
        "status",
    )
    writer.writerow(fields)
    for order in orders:
        writer.writerow([order.get(field) for field in fields])
    return buffer.getvalue()


def _report_html(payload: dict[str, object]) -> str:
    """Render a simple HTML paper report (SP 4.87)."""
    status = payload["status"]
    assert isinstance(status, dict)
    orders = payload["orders"]
    assert isinstance(orders, list)
    status_rows = "".join(
        f"<tr><td>{key}</td><td>{_html_escape(value)}</td></tr>" for key, value in status.items()
    )
    order_rows = ""
    for order in orders:
        order_rows += (
            "<tr>"
            + "".join(
                f"<td>{_html_escape(order.get(field))}</td>"
                for field in ("order_id", "market", "symbol", "side", "quantity", "status")
            )
            + "</tr>"
        )
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Harbor paper report</title>'
        "</head><body>"
        f'<h1>Paper run report</h1><table border="1">{status_rows}</table>'
        f'<h2>Orders</h2><table border="1"><tr><th>order_id</th><th>market</th>'
        "<th>symbol</th><th>side</th><th>quantity</th><th>status</th></tr>"
        f"{order_rows}</table></body></html>"
    )


def _html_escape(value: object) -> str:
    """Escape a value for safe HTML rendering."""
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def paper_report_command(
    *,
    store: PaperStore,
    run_id: str,
    report_format: str = "json",
) -> str:
    """Render a paper run report as JSON, CSV or HTML (SP 4.87)."""
    _require_run(store, run_id)
    if report_format == "json":
        return json.dumps(_report_payload(store, run_id), sort_keys=True, ensure_ascii=False)
    if report_format == "csv":
        return _orders_csv(paper_order_list_command(store=store, run_id=run_id))
    if report_format == "html":
        return _report_html(_report_payload(store, run_id))
    raise PaperServiceError(f"Unsupported report format {report_format!r}.")
