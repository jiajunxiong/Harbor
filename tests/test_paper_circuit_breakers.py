"""Daily and monthly circuit breaker tests (MVP 4 / SP 4.37-4.38).

Verifies that a single-day loss trips the daily breaker (freezing new orders)
and a month-to-date loss trips the monthly breaker (freezing plus an
independent review requirement).
"""

import unittest
from datetime import date

from harbor.core.paper_circuit_breakers import (
    BreakerAssessment,
    PaperBreakerError,
    evaluate_daily_breaker,
    evaluate_monthly_breaker,
)
from harbor.core.paper_domain import CircuitBreakerKind

_AS_OF = date(2026, 1, 15)


class DailyBreakerTests(unittest.TestCase):
    """The daily circuit breaker (SP 4.37)."""

    def test_daily_loss_trips_breaker(self) -> None:
        assessment = evaluate_daily_breaker(as_of=_AS_OF, daily_loss_pct=0.06)
        self.assertIsInstance(assessment, BreakerAssessment)
        self.assertEqual(assessment.kind, CircuitBreakerKind.DAILY)
        self.assertTrue(assessment.triggered)
        self.assertFalse(assessment.requires_review)
        self.assertIn("freezing new orders", assessment.reason)

    def test_daily_loss_within_threshold(self) -> None:
        assessment = evaluate_daily_breaker(as_of=_AS_OF, daily_loss_pct=0.04)
        self.assertFalse(assessment.triggered)

    def test_daily_boundary_not_tripped(self) -> None:
        # strictly greater than the threshold trips the breaker
        assessment = evaluate_daily_breaker(as_of=_AS_OF, daily_loss_pct=0.05)
        self.assertFalse(assessment.triggered)

    def test_custom_threshold(self) -> None:
        assessment = evaluate_daily_breaker(as_of=_AS_OF, daily_loss_pct=0.04, threshold=0.03)
        self.assertTrue(assessment.triggered)

    def test_invalid_inputs(self) -> None:
        with self.assertRaises(PaperBreakerError):
            evaluate_daily_breaker(as_of=_AS_OF, daily_loss_pct=-0.01)
        with self.assertRaises(PaperBreakerError):
            evaluate_daily_breaker(as_of=_AS_OF, daily_loss_pct=0.05, threshold=1.5)

    def test_readable(self) -> None:
        assessment = evaluate_daily_breaker(as_of=_AS_OF, daily_loss_pct=0.06)
        self.assertIn("DAILY breaker TRIGGERED", assessment.readable())


class MonthlyBreakerTests(unittest.TestCase):
    """The monthly circuit breaker (SP 4.38)."""

    def test_monthly_loss_trips_breaker_with_review(self) -> None:
        assessment = evaluate_monthly_breaker(as_of=_AS_OF, monthly_loss_pct=0.12)
        self.assertEqual(assessment.kind, CircuitBreakerKind.MONTHLY)
        self.assertTrue(assessment.triggered)
        self.assertTrue(assessment.requires_review)
        self.assertIn("independent review", assessment.reason)

    def test_monthly_loss_within_threshold(self) -> None:
        assessment = evaluate_monthly_breaker(as_of=_AS_OF, monthly_loss_pct=0.08)
        self.assertFalse(assessment.triggered)
        self.assertFalse(assessment.requires_review)

    def test_custom_threshold(self) -> None:
        assessment = evaluate_monthly_breaker(as_of=_AS_OF, monthly_loss_pct=0.05, threshold=0.04)
        self.assertTrue(assessment.triggered)

    def test_readable(self) -> None:
        assessment = evaluate_monthly_breaker(as_of=_AS_OF, monthly_loss_pct=0.12)
        self.assertIn("independent review required", assessment.readable())
