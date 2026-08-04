"""Adjusted factor and equity reconciliation check tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.quality_checks import (
    reconcile_adjusted_factors,
    reconcile_equity_events,
)
from harbor.core.validation import QualityFinding


def _factor(symbol: str, day: date, cumulative: float, daily: float = 1.0) -> dict[str, object]:
    return {
        "market": "US",
        "symbol": symbol,
        "date": day,
        "daily_factor": daily,
        "cumulative_factor": cumulative,
    }


def _entitlement(
    symbol: str,
    action_id: str,
    position_date: date,
    quantity: float,
    cash: float = 0.0,
) -> dict[str, object]:
    return {
        "market": "US",
        "symbol": symbol,
        "position_date": position_date,
        "action_id": action_id,
        "entitled_quantity": quantity,
        "cash_amount": cash,
    }


def _check_names(findings: list[QualityFinding]) -> list[str]:
    return [finding.check_name for finding in findings]


class AdjustedFactorReconciliationTests(unittest.TestCase):
    """Verify adjusted factor reconciliation (SP 1.94)."""

    def test_matching_factors_have_no_findings(self) -> None:
        computed = [_factor("AAPL", date(2026, 1, 5), 0.5)]
        reference = [_factor("AAPL", date(2026, 1, 5), 0.5)]
        self.assertEqual(reconcile_adjusted_factors(MarketTarget.US, computed, reference), [])

    def test_mismatched_cumulative_factor_is_flagged(self) -> None:
        computed = [_factor("AAPL", date(2026, 1, 5), 0.6)]
        reference = [_factor("AAPL", date(2026, 1, 5), 0.5)]
        findings = reconcile_adjusted_factors(MarketTarget.US, computed, reference)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check_name, "adjusted_factor_mismatch")
        self.assertEqual(findings[0].severity, "error")
        self.assertEqual(findings[0].symbol, "AAPL")

    def test_computed_without_reference_is_unreconciled(self) -> None:
        computed = [_factor("AAPL", date(2026, 1, 6), 1.0)]
        reference = [_factor("AAPL", date(2026, 1, 5), 0.5)]
        findings = reconcile_adjusted_factors(MarketTarget.US, computed, reference)
        names = _check_names(findings)
        self.assertIn("adjusted_factor_unreconciled", names)

    def test_reference_without_computed_is_missing(self) -> None:
        computed = [_factor("AAPL", date(2026, 1, 5), 0.5)]
        reference = [_factor("AAPL", date(2026, 1, 5), 0.5), _factor("AAPL", date(2026, 1, 6), 1.0)]
        findings = reconcile_adjusted_factors(MarketTarget.US, computed, reference)
        self.assertIn("adjusted_factor_missing", _check_names(findings))

    def test_tolerance_controls_agreement(self) -> None:
        computed = [_factor("AAPL", date(2026, 1, 5), 0.55)]
        reference = [_factor("AAPL", date(2026, 1, 5), 0.5)]
        self.assertEqual(
            reconcile_adjusted_factors(MarketTarget.US, computed, reference, rel_tol=0.1), []
        )
        self.assertIn(
            "adjusted_factor_mismatch",
            _check_names(reconcile_adjusted_factors(MarketTarget.US, computed, reference)),
        )


class EquityReconciliationTests(unittest.TestCase):
    """Verify equity entitlement reconciliation (SP 1.94)."""

    def test_matching_entitlements_have_no_findings(self) -> None:
        computed = [_entitlement("AAPL", "AAPL-split-1", date(2026, 1, 5), 200.0)]
        reference = [_entitlement("AAPL", "AAPL-split-1", date(2026, 1, 5), 200.0)]
        self.assertEqual(reconcile_equity_events(MarketTarget.US, computed, reference), [])

    def test_mismatched_cash_amount_is_flagged(self) -> None:
        computed = [_entitlement("AAPL", "AAPL-div-1", date(2026, 1, 5), 0.0, cash=100.0)]
        reference = [_entitlement("AAPL", "AAPL-div-1", date(2026, 1, 5), 0.0, cash=99.0)]
        findings = reconcile_equity_events(MarketTarget.US, computed, reference)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check_name, "equity_event_mismatch")
        self.assertEqual(findings[0].severity, "error")

    def test_computed_without_reference_is_unreconciled(self) -> None:
        computed = [_entitlement("AAPL", "AAPL-split-1", date(2026, 1, 5), 200.0)]
        findings = reconcile_equity_events(MarketTarget.US, computed, [])
        self.assertIn("equity_event_unreconciled", _check_names(findings))

    def test_reference_without_computed_is_missing(self) -> None:
        computed = [_entitlement("AAPL", "AAPL-split-1", date(2026, 1, 5), 200.0)]
        reference = [
            _entitlement("AAPL", "AAPL-split-1", date(2026, 1, 5), 200.0),
            _entitlement("AAPL", "AAPL-div-1", date(2026, 1, 5), 0.0, cash=100.0),
        ]
        findings = reconcile_equity_events(MarketTarget.US, computed, reference)
        self.assertIn("equity_event_missing", _check_names(findings))

    def test_hk_reconciliation(self) -> None:
        computed = [_entitlement("0700.HK", "0700.HK-rights-1", date(2026, 1, 5), 50.0)]
        reference = [_entitlement("0700.HK", "0700.HK-rights-1", date(2026, 1, 5), 60.0)]
        findings = reconcile_equity_events(MarketTarget.HK, computed, reference)
        self.assertIn("equity_event_mismatch", _check_names(findings))
