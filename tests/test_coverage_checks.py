"""Coverage and price timeliness quality check tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.quality_checks import find_coverage_gaps, find_stale_quotes


def _quote(symbol: str, day: date) -> dict[str, object]:
    return {"market": "US", "symbol": symbol, "date": day}


class CoverageGapTests(unittest.TestCase):
    """Verify coverage detection (SP 1.89 HK, 1.90 US)."""

    def test_hk_full_coverage_has_no_findings(self) -> None:
        expected = ["0001.HK", "0700.HK", "0005.HK"]
        covered = ["0001.HK", "0700.HK", "0005.HK"]
        self.assertEqual(find_coverage_gaps(MarketTarget.HK, expected, covered), [])

    def test_hk_missing_security_is_flagged(self) -> None:
        expected = ["0001.HK", "0700.HK", "0005.HK"]
        covered = ["0001.HK", "0700.HK"]
        findings = find_coverage_gaps(MarketTarget.HK, expected, covered)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.check_name, "coverage_gap")
        self.assertEqual(finding.severity, "error")
        self.assertEqual(finding.symbol, "0005.HK")

    def test_us_missing_securities_are_flagged(self) -> None:
        expected = ["AAPL", "MSFT", "GOOGL"]
        covered = ["AAPL"]
        findings = find_coverage_gaps(MarketTarget.US, expected, covered)
        self.assertEqual([finding.symbol for finding in findings], ["GOOGL", "MSFT"])

    def test_duplicate_covered_symbols_are_tolerated(self) -> None:
        expected = ["AAPL"]
        covered = ["AAPL", "AAPL"]
        self.assertEqual(find_coverage_gaps(MarketTarget.US, expected, covered), [])


class StaleQuoteTests(unittest.TestCase):
    """Verify price timeliness detection (SP 1.89 HK, 1.90 US)."""

    def test_fresh_quote_has_no_findings(self) -> None:
        rows = [_quote("0700.HK", date(2026, 1, 5))]
        findings = find_stale_quotes(MarketTarget.HK, rows, as_of=date(2026, 1, 9))
        self.assertEqual(findings, [])

    def test_hk_stale_quote_is_flagged(self) -> None:
        rows = [_quote("0700.HK", date(2026, 1, 1))]
        findings = find_stale_quotes(MarketTarget.HK, rows, as_of=date(2026, 1, 9))
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.check_name, "stale_quote")
        self.assertEqual(finding.severity, "warning")
        self.assertEqual(finding.symbol, "0700.HK")
        self.assertIn("8 days older", finding.details)

    def test_us_stale_quote_is_flagged(self) -> None:
        rows = [_quote("AAPL", date(2026, 1, 1))]
        findings = find_stale_quotes(MarketTarget.US, rows, as_of=date(2026, 1, 9))
        self.assertEqual(len(findings), 1)

    def test_custom_max_age_is_applied(self) -> None:
        rows = [_quote("AAPL", date(2026, 1, 5))]
        findings = find_stale_quotes(MarketTarget.US, rows, as_of=date(2026, 1, 9), max_age_days=3)
        self.assertEqual(len(findings), 1)
        self.assertIn("4 days older", findings[0].details)

    def test_latest_quote_per_symbol_is_used(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 1)),
            _quote("AAPL", date(2026, 1, 8)),
        ]
        findings = find_stale_quotes(MarketTarget.US, rows, as_of=date(2026, 1, 9))
        self.assertEqual(findings, [])

    def test_future_dated_quote_is_not_stale(self) -> None:
        rows = [_quote("AAPL", date(2026, 1, 12))]
        findings = find_stale_quotes(MarketTarget.US, rows, as_of=date(2026, 1, 9))
        self.assertEqual(findings, [])
