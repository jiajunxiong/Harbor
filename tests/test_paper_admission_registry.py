"""Difference verification admission registry tests (MVP 4 / SP 4.77).

Covers the run-month, per-market rebalance / fill / coverage minima, the
unresolved-reconciliation-difference limit and the independent-waiver path.
"""

import unittest
from datetime import date

from harbor.core.paper_admission_registry import (
    AdmissionRequirement,
    AdmissionWaiver,
    MarketAdmissionStatus,
    PaperAdmissionError,
    VerificationAdmission,
    assess_verification_admission,
)

_START = date(2025, 1, 1)
_AS_OF = date(2026, 1, 15)  # 12 full months


def _passing(**kwargs: object) -> VerificationAdmission:
    values: dict[str, object] = {
        "paper_run_id": "paper-1",
        "as_of": _AS_OF,
        "started_at": _START,
        "fills": {"HK": 40, "US": 35},
        "rebalances": {"HK": 5, "US": 4},
        "coverage": {"HK": 0.95, "US": 0.92},
        "unresolved_reconciliation_differences": 0,
    }
    values.update(kwargs)
    return assess_verification_admission(**values)  # type: ignore[arg-type]


class AdmissionRegistryTests(unittest.TestCase):
    """The verification admission assessment (SP 4.77)."""

    def test_passing_admission(self) -> None:
        assessment = _passing()
        self.assertIsInstance(assessment, VerificationAdmission)
        self.assertTrue(assessment.passed)
        self.assertEqual(assessment.run_months, 12)
        self.assertEqual(assessment.reasons, ())
        self.assertEqual(len(assessment.per_market), 2)

    def test_run_months_below_minimum_blocks(self) -> None:
        assessment = _passing(as_of=date(2025, 6, 15))
        self.assertFalse(assessment.passed)
        self.assertIn("month", assessment.reasons[0])

    def test_per_market_minima_block(self) -> None:
        assessment = _passing(fills={"HK": 2, "US": 35}, rebalances={"HK": 1, "US": 4})
        self.assertFalse(assessment.passed)
        self.assertTrue(any("HK" in reason for reason in assessment.reasons))

    def test_coverage_below_minimum_blocks(self) -> None:
        assessment = _passing(coverage={"HK": 0.5, "US": 0.92})
        self.assertFalse(assessment.passed)

    def test_unresolved_differences_block(self) -> None:
        assessment = _passing(unresolved_reconciliation_differences=2)
        self.assertFalse(assessment.passed)
        self.assertTrue(any("unresolved" in reason for reason in assessment.reasons))

    def test_waiver_grants_pass(self) -> None:
        waiver = AdmissionWaiver(
            requirement="run_months",
            approver="risk-committee",
            reason="data-feed delay outside the strategy's control",
            granted_at=_AS_OF,
        )
        assessment = _passing(as_of=date(2025, 6, 15), waivers=(waiver,))
        self.assertTrue(assessment.passed)

    def test_market_waiver_grants_pass(self) -> None:
        waiver = AdmissionWaiver(
            requirement="market",
            approver="risk-committee",
            reason="low-liquidity market waived independently",
            granted_at=_AS_OF,
        )
        assessment = _passing(
            fills={"HK": 2, "US": 35},
            rebalances={"HK": 1, "US": 4},
            waivers=(waiver,),
        )
        self.assertTrue(assessment.passed)

    def test_market_status_readable(self) -> None:
        assessment = _passing()
        status = assessment.per_market[0]
        self.assertIsInstance(status, MarketAdmissionStatus)
        self.assertTrue(status.passed)
        self.assertIn("passed", status.readable())

    def test_readable(self) -> None:
        assessment = _passing(as_of=date(2025, 6, 15))
        self.assertIn("BLOCKED", assessment.readable())

    def test_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(PaperAdmissionError):
            _passing(paper_run_id="")
        with self.assertRaises(PaperAdmissionError):
            _passing(as_of=date(2024, 12, 1))
        with self.assertRaises(PaperAdmissionError):
            _passing(unresolved_reconciliation_differences=-1)

    def test_invalid_requirement_rejected(self) -> None:
        with self.assertRaises(PaperAdmissionError):
            AdmissionRequirement(min_run_months=0)
        with self.assertRaises(PaperAdmissionError):
            AdmissionRequirement(min_coverage_pct=1.5)

    def test_invalid_waiver_rejected(self) -> None:
        with self.assertRaises(PaperAdmissionError):
            AdmissionWaiver(requirement="", approver="a", reason="r", granted_at=_AS_OF)
