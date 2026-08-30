"""Paper-trading domain type tests (MVP 4 / SP 4.1).

Verifies the immutable value types that give the paper-trading loop its shared
vocabulary: the paper status / order-status / price-type enums (SP 4.10 / 4.6),
the run master record (SP 4.5 / 4.9), the account (SP 4.4), positions, signal
intentions (SP 4.14), orders, fills, risk approvals (SP 4.7 / 4.39) and
circuit-breaker state (SP 4.7). Every type is immutable and each rejects
invalid field values instead of silently normalizing them.
"""

import unittest
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_domain import (
    ApprovalDecision,
    CircuitBreakerKind,
    CircuitBreakerState,
    PaperAccount,
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperPosition,
    PaperPriceType,
    PaperRun,
    PaperRunMode,
    PaperStatus,
    RiskApproval,
    SignalIntention,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _run(**overrides: object) -> PaperRun:
    """Return a valid paper run with overridable fields."""
    fields: dict[str, object] = {
        "run_id": "paper-1",
        "strategy": "mvp3-qualified",
        "strategy_version": "1.0.0",
        "config_hash": "abc123",
        "dataset_fingerprint": "fp-1",
        "code_version": "1.0.0",
        "markets": (Market.HK, Market.US),
        "base_currency": Currency.HKD,
        "status": PaperStatus.DRAFT,
        "created_at": _utc_at(2026, 1, 1),
    }
    fields.update(overrides)
    return PaperRun(**fields)  # type: ignore[arg-type]


def _account(**overrides: object) -> PaperAccount:
    """Return a valid paper account with overridable fields."""
    fields: dict[str, object] = {
        "account_id": "acc-1",
        "base_currency": Currency.HKD,
        "currencies": (Currency.HKD, Currency.USD),
        "initial_capital": 1_000_000.0,
        "opened_at": date(2026, 1, 1),
    }
    fields.update(overrides)
    return PaperAccount(**fields)  # type: ignore[arg-type]


def _position(**overrides: object) -> PaperPosition:
    """Return a valid paper position with overridable fields."""
    fields: dict[str, object] = {
        "market": Market.HK,
        "symbol": "0001.HK",
        "quantity": 100.0,
        "average_cost": 50.0,
        "currency": Currency.HKD,
        "acquired_at": date(2026, 1, 2),
    }
    fields.update(overrides)
    return PaperPosition(**fields)  # type: ignore[arg-type]


def _intention(**overrides: object) -> SignalIntention:
    """Return a valid signal intention with overridable fields."""
    fields: dict[str, object] = {
        "intention_id": "sig-1",
        "paper_run_id": "paper-1",
        "strategy": "mvp3-qualified",
        "strategy_version": "1.0.0",
        "market": Market.HK,
        "rebalance_date": date(2026, 1, 2),
        "target_weights": (("0001.HK", 0.5), ("0002.HK", 0.5)),
        "source_run_id": "mvp3-run-1",
        "created_at": _utc_at(2026, 1, 1, 12),
    }
    fields.update(overrides)
    return SignalIntention(**fields)  # type: ignore[arg-type]


def _order(**overrides: object) -> PaperOrder:
    """Return a valid paper order with overridable fields."""
    fields: dict[str, object] = {
        "order_id": "order-1",
        "paper_run_id": "paper-1",
        "market": Market.HK,
        "symbol": "0001.HK",
        "side": OrderSide.BUY,
        "quantity": 100.0,
        "currency": Currency.HKD,
        "price_type": PaperPriceType.REFERENCE,
        "status": PaperOrderStatus.CREATED,
        "created_at": _utc_at(2026, 1, 2, 9),
        "intention_id": "sig-1",
    }
    fields.update(overrides)
    return PaperOrder(**fields)  # type: ignore[arg-type]


def _fill(**overrides: object) -> PaperFill:
    """Return a valid paper fill with overridable fields."""
    fields: dict[str, object] = {
        "fill_id": "fill-1",
        "paper_order_id": "order-1",
        "paper_run_id": "paper-1",
        "market": Market.HK,
        "symbol": "0001.HK",
        "side": OrderSide.BUY,
        "quantity": 100.0,
        "price": 50.0,
        "currency": Currency.HKD,
        "trade_date": date(2026, 1, 2),
        "fee": 5.0,
    }
    fields.update(overrides)
    return PaperFill(**fields)  # type: ignore[arg-type]


def _approval(**overrides: object) -> RiskApproval:
    """Return a valid risk approval with overridable fields."""
    fields: dict[str, object] = {
        "approval_id": "ap-1",
        "paper_run_id": "paper-1",
        "scope": "run:paper-1",
        "approver": "alice",
        "decision": ApprovalDecision.APPROVED,
        "rule": "drawdown_circuit",
        "reason": "reviewed and accepted",
        "decided_at": _utc_at(2026, 1, 3, 10),
    }
    fields.update(overrides)
    return RiskApproval(**fields)  # type: ignore[arg-type]


def _breaker(**overrides: object) -> CircuitBreakerState:
    """Return a valid circuit-breaker state with overridable fields."""
    fields: dict[str, object] = {
        "breaker_id": "cb-1",
        "paper_run_id": "paper-1",
        "kind": CircuitBreakerKind.DRAWDOWN,
        "triggered": True,
        "scope": "new_orders",
        "reason": "drawdown 10%",
        "frozen_at": _utc_at(2026, 2, 1, 9),
    }
    fields.update(overrides)
    return CircuitBreakerState(**fields)  # type: ignore[arg-type]


class PaperStatusTests(unittest.TestCase):
    """The SP 4.10 status vocabulary is fixed."""

    def test_status_values(self) -> None:
        self.assertEqual(
            [status.value for status in PaperStatus],
            ["DRAFT", "APPROVED", "ACTIVE", "PAUSED", "CIRCUIT_BROKEN", "STOPPED"],
        )

    def test_run_mode_values(self) -> None:
        self.assertEqual(
            [mode.value for mode in PaperRunMode],
            ["MANUAL", "AUTO"],
        )

    def test_order_status_values(self) -> None:
        self.assertEqual(
            [status.value for status in PaperOrderStatus],
            [
                "CREATED",
                "SUBMITTED",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELLED",
                "REJECTED",
            ],
        )

    def test_price_type_values(self) -> None:
        self.assertEqual(
            [price_type.value for price_type in PaperPriceType],
            ["MARKET", "LIMIT", "REFERENCE"],
        )

    def test_approval_and_breaker_enums(self) -> None:
        self.assertEqual([d.value for d in ApprovalDecision], ["APPROVED", "REJECTED"])
        self.assertEqual([k.value for k in CircuitBreakerKind], ["DAILY", "MONTHLY", "DRAWDOWN"])


class PaperRunTests(unittest.TestCase):
    """The paper run master record (SP 4.5 / 4.9)."""

    def test_valid_run(self) -> None:
        run = _run()
        self.assertEqual(run.run_id, "paper-1")
        self.assertEqual(run.strategy, "mvp3-qualified")
        self.assertEqual(run.status, PaperStatus.DRAFT)
        self.assertEqual(run.markets, (Market.HK, Market.US))
        self.assertIn("paper run paper-1", run.readable())
        self.assertIn("fp-1", run.readable())

    def test_empty_identity_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _run(run_id="")
        with self.assertRaises(ValueError):
            _run(config_hash="")
        with self.assertRaises(ValueError):
            _run(dataset_fingerprint="")
        with self.assertRaises(ValueError):
            _run(code_version="")

    def test_market_scope(self) -> None:
        with self.assertRaises(ValueError):
            _run(markets=())
        with self.assertRaises(ValueError):
            _run(markets=(Market.HK, Market.HK))

    def test_timestamps(self) -> None:
        with self.assertRaises(ValueError):
            _run(stopped_at=_utc_at(2025, 12, 31), started_at=_utc_at(2026, 1, 1))

    def test_immutable(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            _run().run_id = "changed"  # type: ignore[misc]


class PaperAccountTests(unittest.TestCase):
    """The paper account identity (SP 4.1 / 4.4)."""

    def test_valid_account(self) -> None:
        account = _account()
        self.assertEqual(account.base_currency, Currency.HKD)
        self.assertEqual(account.currencies, (Currency.HKD, Currency.USD))
        self.assertIn("paper account acc-1", account.readable())

    def test_base_currency_must_be_in_ledger(self) -> None:
        with self.assertRaises(ValueError):
            _account(base_currency=Currency.USD, currencies=(Currency.HKD,))

    def test_empty_or_duplicate_currencies(self) -> None:
        with self.assertRaises(ValueError):
            _account(currencies=())
        with self.assertRaises(ValueError):
            _account(currencies=(Currency.HKD, Currency.HKD))

    def test_negative_capital(self) -> None:
        with self.assertRaises(ValueError):
            _account(initial_capital=-1.0)


class PaperPositionTests(unittest.TestCase):
    """The paper position with cost basis (SP 4.1 / 4.4)."""

    def test_valid_position_and_cost_basis(self) -> None:
        position = _position(quantity=100.0, average_cost=50.0)
        self.assertEqual(position.cost_basis, 5000.0)
        self.assertIn("0001.HK", position.readable())

    def test_invalid_position(self) -> None:
        with self.assertRaises(ValueError):
            _position(symbol="")
        with self.assertRaises(ValueError):
            _position(quantity=-1.0)
        with self.assertRaises(ValueError):
            _position(average_cost=-1.0)


class SignalIntentionTests(unittest.TestCase):
    """The normalized signal intention (SP 4.1 / 4.14)."""

    def test_valid_intention(self) -> None:
        intention = _intention()
        self.assertEqual(intention.weight_of("0001.HK"), 0.5)
        self.assertIsNone(intention.weight_of("9999.HK"))
        self.assertIn("sig-1", intention.readable())
        self.assertIn("mvp3-run-1", intention.readable())

    def test_invalid_intention(self) -> None:
        with self.assertRaises(ValueError):
            _intention(intention_id="")
        with self.assertRaises(ValueError):
            _intention(paper_run_id="")
        with self.assertRaises(ValueError):
            _intention(strategy_version="")
        with self.assertRaises(ValueError):
            _intention(target_weights=(("0001.HK", 0.5), ("0001.HK", 0.5)))
        with self.assertRaises(ValueError):
            _intention(target_weights=(("0001.HK", -0.1),))


class PaperOrderTests(unittest.TestCase):
    """The paper order (SP 4.1 / 4.6)."""

    def test_valid_order(self) -> None:
        order = _order()
        self.assertEqual(order.side, OrderSide.BUY)
        self.assertEqual(order.status, PaperOrderStatus.CREATED)
        self.assertEqual(order.intention_id, "sig-1")
        self.assertIn("order order-1", order.readable())

    def test_invalid_order(self) -> None:
        with self.assertRaises(ValueError):
            _order(order_id="")
        with self.assertRaises(ValueError):
            _order(paper_run_id="")
        with self.assertRaises(ValueError):
            _order(symbol="")
        with self.assertRaises(ValueError):
            _order(quantity=0.0)
        with self.assertRaises(ValueError):
            _order(quantity=-5.0)


class PaperFillTests(unittest.TestCase):
    """The executed paper order (SP 4.1 / 4.6)."""

    def test_valid_fill_and_notional(self) -> None:
        fill = _fill(quantity=100.0, price=50.0, fee=5.0)
        self.assertEqual(fill.notional, 5000.0)
        self.assertEqual(fill.fee, 5.0)
        self.assertIn("fill fill-1", fill.readable())

    def test_invalid_fill(self) -> None:
        with self.assertRaises(ValueError):
            _fill(fill_id="")
        with self.assertRaises(ValueError):
            _fill(paper_order_id="")
        with self.assertRaises(ValueError):
            _fill(symbol="")
        with self.assertRaises(ValueError):
            _fill(quantity=0.0)
        with self.assertRaises(ValueError):
            _fill(price=-1.0)
        with self.assertRaises(ValueError):
            _fill(fee=-1.0)


class RiskApprovalTests(unittest.TestCase):
    """The recorded risk approval (SP 4.1 / 4.7 / 4.39)."""

    def test_valid_approval(self) -> None:
        approval = _approval()
        self.assertEqual(approval.decision, ApprovalDecision.APPROVED)
        self.assertEqual(approval.scope, "run:paper-1")
        self.assertIn("alice", approval.readable())

    def test_invalid_approval(self) -> None:
        with self.assertRaises(ValueError):
            _approval(approval_id="")
        with self.assertRaises(ValueError):
            _approval(paper_run_id="")
        with self.assertRaises(ValueError):
            _approval(approver="")
        with self.assertRaises(ValueError):
            _approval(rule="")
        with self.assertRaises(ValueError):
            _approval(decided_at=datetime(2026, 1, 3, 10))


class CircuitBreakerStateTests(unittest.TestCase):
    """The circuit-breaker state (SP 4.1 / 4.7)."""

    def test_valid_triggered_breaker(self) -> None:
        breaker = _breaker()
        self.assertTrue(breaker.triggered)
        self.assertIn("TRIGGERED", breaker.readable())

    def test_valid_recovered_breaker(self) -> None:
        breaker = _breaker(
            triggered=False,
            recovered_at=_utc_at(2026, 2, 2, 9),
        )
        self.assertFalse(breaker.triggered)
        self.assertIn("RECOVERED", breaker.readable())

    def test_invalid_breaker(self) -> None:
        with self.assertRaises(ValueError):
            _breaker(breaker_id="")
        with self.assertRaises(ValueError):
            _breaker(reason="")
        with self.assertRaises(ValueError):
            _breaker(frozen_at=datetime(2026, 2, 1, 9))
        with self.assertRaises(ValueError):
            _breaker(triggered=True, recovered_at=_utc_at(2026, 2, 2))
        with self.assertRaises(ValueError):
            _breaker(triggered=False)
        with self.assertRaises(ValueError):
            _breaker(triggered=False, recovered_at=_utc_at(2026, 1, 31, 9))
