"""Daily breaker suite (MVP 4 / SP 4.44).

Verifies the daily circuit-breaker chain: a single-day loss trips the breaker,
freezes new orders, produces an audit event, and recovery unfreezes the run.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_audit import (
    AuditActor,
    AuditEventType,
    audit_fingerprint,
    new_paper_audit_log,
)
from harbor.core.paper_circuit_breakers import evaluate_daily_breaker
from harbor.core.paper_domain import PaperOrder, PaperOrderStatus, PaperPriceType
from harbor.core.paper_freeze import freeze_run, recover_freeze, reject_during_freeze
from harbor.core.paper_risk_gate import gate_order


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _order() -> PaperOrder:
    return PaperOrder(
        order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=OrderSide.BUY,
        quantity=100.0,
        currency=Currency.HKD,
        price_type=PaperPriceType.REFERENCE,
        status=PaperOrderStatus.CREATED,
        created_at=_utc_at(2026, 1, 3, 9),
        intention_id="sig-1",
    )


class DailyBreakerSuiteTests(unittest.TestCase):
    """The daily breaker chain (SP 4.44)."""

    def test_daily_loss_trips_and_freezes(self) -> None:
        assessment = evaluate_daily_breaker(as_of=date(2026, 1, 3), daily_loss_pct=0.08)
        self.assertTrue(assessment.triggered)
        self.assertFalse(assessment.requires_review)
        freeze = freeze_run(
            paper_run_id="paper-1",
            reason="daily loss exceeded threshold",
            frozen_at=_utc_at(2026, 1, 3, 16),
        )
        self.assertTrue(freeze.blocks_new_orders())

    def test_freeze_rejects_new_order(self) -> None:
        freeze = freeze_run(
            paper_run_id="paper-1",
            reason="daily loss exceeded threshold",
            frozen_at=_utc_at(2026, 1, 3, 16),
        )
        outcome = gate_order(order=_order(), freeze=freeze)
        self.assertFalse(outcome.allowed)
        self.assertIsNotNone(reject_during_freeze(freeze))

    def test_audit_event_recorded(self) -> None:
        log = new_paper_audit_log("paper-1", recorded_at=_utc_at(2026, 1, 3, 8))
        log = log.append(
            event_id="e-breaker",
            event_type=AuditEventType.CIRCUIT_BREAKER,
            actor=AuditActor.SYSTEM,
            detail="daily circuit breaker tripped; new orders frozen",
            recorded_at=_utc_at(2026, 1, 3, 16),
        )
        self.assertEqual(len(log.events_for(AuditEventType.CIRCUIT_BREAKER)), 1)
        self.assertEqual(audit_fingerprint(log), audit_fingerprint(log))  # replayable

    def test_recovery_unfreezes(self) -> None:
        freeze = freeze_run(
            paper_run_id="paper-1",
            reason="daily loss",
            frozen_at=_utc_at(2026, 1, 3, 16),
        )
        recovered = recover_freeze(state=freeze, recovered_at=_utc_at(2026, 1, 6, 9))
        self.assertFalse(recovered.blocks_new_orders())
        outcome = gate_order(order=_order(), freeze=recovered)
        self.assertTrue(outcome.allowed)
