"""Dividend timing and amount consistency check tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.quality_checks import find_inconsistent_dividends
from harbor.core.validation import QualityFinding


def _dividend(symbol: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "market": "HK",
        "symbol": symbol,
        "announce_date": date(2026, 1, 1),
        "ex_date": date(2026, 1, 7),
        "record_date": date(2026, 1, 9),
        "payment_date": date(2026, 1, 19),
        "amount": 2.0,
        "type": "regular",
        "currency": "HKD",
    }
    row.update(overrides)
    return row


def _check_names(findings: list[QualityFinding]) -> list[str]:
    return [finding.check_name for finding in findings]


class DividendConsistencyTests(unittest.TestCase):
    """Verify dividend timing and amount consistency (SP 1.91)."""

    def test_valid_hk_dividend_has_no_findings(self) -> None:
        self.assertEqual(find_inconsistent_dividends(MarketTarget.HK, [_dividend("0001.HK")]), [])

    def test_valid_us_dividend_has_no_findings(self) -> None:
        row = _dividend(
            "AAPL",
            market="US",
            amount=1.0,
            currency="USD",
        )
        self.assertEqual(find_inconsistent_dividends(MarketTarget.US, [row]), [])

    def test_non_positive_amount_is_flagged(self) -> None:
        row = _dividend("0001.HK", amount=0.0)
        findings = find_inconsistent_dividends(MarketTarget.HK, [row])
        self.assertIn("dividend_amount_invalid", _check_names(findings))
        self.assertEqual(findings[0].severity, "error")

    def test_record_date_before_ex_date_is_flagged(self) -> None:
        row = _dividend("0001.HK", ex_date=date(2026, 1, 9), record_date=date(2026, 1, 7))
        findings = find_inconsistent_dividends(MarketTarget.HK, [row])
        self.assertIn("dividend_date_invalid", _check_names(findings))

    def test_payment_date_before_ex_date_is_flagged(self) -> None:
        row = _dividend("0001.HK", ex_date=date(2026, 1, 9), payment_date=date(2026, 1, 7))
        findings = find_inconsistent_dividends(MarketTarget.HK, [row])
        self.assertIn("dividend_date_invalid", _check_names(findings))

    def test_ex_date_on_or_before_announcement_is_flagged(self) -> None:
        row = _dividend("0001.HK", announce_date=date(2026, 1, 7))
        findings = find_inconsistent_dividends(MarketTarget.HK, [row])
        self.assertIn("dividend_date_invalid", _check_names(findings))

    def test_hk_special_dividend_too_small_is_flagged(self) -> None:
        row = _dividend("0001.HK", amount=0.3, type="special")
        findings = find_inconsistent_dividends(MarketTarget.HK, [row])
        self.assertIn("special_flag_unreasonable", _check_names(findings))
        self.assertIn("below the HK minimum", findings[0].details)

    def test_hk_regular_dividend_too_large_is_flagged(self) -> None:
        row = _dividend("0001.HK", amount=4.0, type="regular")
        findings = find_inconsistent_dividends(MarketTarget.HK, [row])
        self.assertIn("special_flag_unreasonable", _check_names(findings))

    def test_us_regular_dividend_too_large_is_flagged(self) -> None:
        row = _dividend("AAPL", market="US", currency="USD", amount=2.0, type="regular")
        findings = find_inconsistent_dividends(MarketTarget.US, [row])
        self.assertIn("special_flag_unreasonable", _check_names(findings))

    def test_hk_and_us_special_rules_differ(self) -> None:
        row = _dividend("0001.HK", amount=0.3, type="special")
        self.assertIn(
            "special_flag_unreasonable",
            _check_names(find_inconsistent_dividends(MarketTarget.HK, [row])),
        )
        us_row = _dividend("AAPL", market="US", currency="USD", amount=0.3, type="special")
        self.assertEqual(find_inconsistent_dividends(MarketTarget.US, [us_row]), [])
