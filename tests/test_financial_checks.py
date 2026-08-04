"""Fundamentals completeness and report date check tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.quality_checks import (
    find_incomplete_financials,
    find_unreasonable_report_dates,
)
from harbor.core.validation import QualityFinding


def _financial(symbol: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "market": "US",
        "symbol": symbol,
        "report_date": date(2025, 12, 31),
        "fiscal_period": "2025",
        "roe": 0.2,
        "net_income": 10.0,
        "total_equity": 50.0,
        "revenue": 100.0,
    }
    row.update(overrides)
    return row


def _check_names(findings: list[QualityFinding]) -> list[str]:
    return [finding.check_name for finding in findings]


class FinancialCompletenessTests(unittest.TestCase):
    """Verify key metric completeness detection (SP 1.92)."""

    def test_complete_financials_have_no_findings(self) -> None:
        self.assertEqual(find_incomplete_financials(MarketTarget.US, [_financial("AAPL")]), [])

    def test_missing_one_metric_is_a_warning(self) -> None:
        row = _financial("AAPL", revenue=None)
        findings = find_incomplete_financials(MarketTarget.US, [row])
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.check_name, "financials_incomplete")
        self.assertEqual(finding.severity, "warning")
        self.assertEqual(finding.symbol, "AAPL")
        self.assertIn("revenue", finding.details)

    def test_missing_all_metrics_is_an_error(self) -> None:
        row = _financial("AAPL", roe=None, net_income=None, total_equity=None, revenue=None)
        findings = find_incomplete_financials(MarketTarget.US, [row])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "error")
        self.assertIn("All key metrics are missing", findings[0].details)

    def test_hk_financials_completeness(self) -> None:
        row = _financial("0700.HK", market="HK", roe=None)
        findings = find_incomplete_financials(MarketTarget.HK, [row])
        self.assertIn("financials_incomplete", _check_names(findings))


class ReportDateTests(unittest.TestCase):
    """Verify report date reasonableness (SP 1.92)."""

    def test_valid_report_date_has_no_findings(self) -> None:
        row = _financial("AAPL")
        findings = find_unreasonable_report_dates(MarketTarget.US, [row], as_of=date(2026, 1, 1))
        self.assertEqual(findings, [])

    def test_future_report_date_is_flagged(self) -> None:
        row = _financial("AAPL", report_date=date(2027, 1, 1), fiscal_period="2027")
        findings = find_unreasonable_report_dates(MarketTarget.US, [row], as_of=date(2026, 1, 1))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check_name, "report_date_unreasonable")
        self.assertEqual(findings[0].severity, "warning")
        self.assertIn("is in the future", findings[0].details)

    def test_fiscal_period_mismatch_is_flagged(self) -> None:
        row = _financial("AAPL", fiscal_period="2024")
        findings = find_unreasonable_report_dates(MarketTarget.US, [row], as_of=date(2026, 1, 1))
        self.assertIn("report_date_unreasonable", _check_names(findings))
        self.assertIn("does not match report year 2025", findings[0].details)

    def test_as_of_defaults_to_today(self) -> None:
        row = _financial("AAPL")
        self.assertEqual(find_unreasonable_report_dates(MarketTarget.US, [row]), [])

    def test_hk_report_date_check(self) -> None:
        row = _financial("0700.HK", market="HK", report_date=date(2030, 12, 31))
        findings = find_unreasonable_report_dates(MarketTarget.HK, [row], as_of=date(2026, 1, 1))
        self.assertIn("report_date_unreasonable", _check_names(findings))
