"""Daily quote quality check tests (duplicates and gaps)."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.quality_checks import (
    check_daily_quotes,
    find_daily_quote_gaps,
    find_duplicate_daily_quotes,
)
from harbor.core.validation import QualityFinding


def _quote(symbol: str, day: date) -> dict[str, object]:
    return {"market": "US", "symbol": symbol, "date": day}


def _check_names(findings: list[QualityFinding]) -> list[str]:
    return [finding.check_name for finding in findings]


class DuplicateDailyQuoteTests(unittest.TestCase):
    """Verify duplicate record detection (SP 1.86 HK, 1.87 US)."""

    def test_hk_duplicate_symbol_date_is_flagged(self) -> None:
        rows = [
            _quote("0700.HK", date(2026, 1, 5)),
            _quote("0700.HK", date(2026, 1, 5)),
        ]
        findings = find_duplicate_daily_quotes(MarketTarget.HK, rows)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.check_name, "daily_quote_duplicate")
        self.assertEqual(finding.severity, "error")
        self.assertEqual(finding.symbol, "0700.HK")
        self.assertIn("2 records", finding.details)

    def test_us_duplicate_symbol_date_is_flagged(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5)),
            _quote("AAPL", date(2026, 1, 5)),
            _quote("AAPL", date(2026, 1, 5)),
        ]
        findings = find_duplicate_daily_quotes(MarketTarget.US, rows)
        self.assertEqual(len(findings), 1)
        self.assertIn("3 records", findings[0].details)

    def test_distinct_symbols_and_dates_are_not_flagged(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5)),
            _quote("AAPL", date(2026, 1, 6)),
            _quote("MSFT", date(2026, 1, 5)),
        ]
        self.assertEqual(find_duplicate_daily_quotes(MarketTarget.US, rows), [])


class DailyQuoteGapTests(unittest.TestCase):
    """Verify trading-day gap detection (SP 1.86 HK, 1.87 US)."""

    def test_hk_weekday_gap_is_flagged(self) -> None:
        rows = [
            _quote("0700.HK", date(2026, 1, 5)),
            _quote("0700.HK", date(2026, 1, 9)),
        ]
        findings = find_daily_quote_gaps(MarketTarget.HK, rows)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.check_name, "daily_quote_gap")
        self.assertEqual(finding.severity, "warning")
        self.assertEqual(finding.symbol, "0700.HK")
        self.assertIn("Missing 3 trading days", finding.details)
        self.assertIn("2026-01-06", finding.details)
        self.assertIn("2026-01-08", finding.details)

    def test_us_weekday_gap_is_flagged(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5)),
            _quote("AAPL", date(2026, 1, 12)),
        ]
        findings = find_daily_quote_gaps(MarketTarget.US, rows)
        self.assertEqual(len(findings), 1)
        self.assertIn("Missing 4 trading days", findings[0].details)

    def test_contiguous_weekdays_have_no_gap(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5)),
            _quote("AAPL", date(2026, 1, 6)),
            _quote("AAPL", date(2026, 1, 7)),
            _quote("AAPL", date(2026, 1, 8)),
            _quote("AAPL", date(2026, 1, 9)),
        ]
        self.assertEqual(find_daily_quote_gaps(MarketTarget.US, rows), [])

    def test_single_quote_has_no_gap(self) -> None:
        rows = [_quote("AAPL", date(2026, 1, 5))]
        self.assertEqual(find_daily_quote_gaps(MarketTarget.US, rows), [])

    def test_weekend_boundary_is_not_a_gap(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5)),
            _quote("AAPL", date(2026, 1, 6)),
        ]
        self.assertEqual(find_daily_quote_gaps(MarketTarget.US, rows), [])


class CombinedDailyQuoteCheckTests(unittest.TestCase):
    """Verify the combined check returns duplicate and gap findings."""

    def test_check_daily_quotes_combines_both_checks(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5)),
            _quote("AAPL", date(2026, 1, 5)),
            _quote("AAPL", date(2026, 1, 9)),
        ]
        findings = check_daily_quotes(MarketTarget.US, rows)
        names = _check_names(findings)
        self.assertIn("daily_quote_duplicate", names)
        self.assertIn("daily_quote_gap", names)

    def test_clean_batch_has_no_findings(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5)),
            _quote("AAPL", date(2026, 1, 6)),
            _quote("MSFT", date(2026, 1, 5)),
            _quote("MSFT", date(2026, 1, 6)),
        ]
        self.assertEqual(check_daily_quotes(MarketTarget.US, rows), [])
