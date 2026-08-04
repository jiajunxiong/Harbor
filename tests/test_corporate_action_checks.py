"""Corporate action lifecycle and terms completeness check tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.quality_checks import find_incomplete_corporate_actions
from harbor.core.validation import QualityFinding


def _action(symbol: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "market": "HK",
        "symbol": symbol,
        "action_id": f"{symbol}-1",
        "announce_date": date(2026, 1, 1),
        "ex_date": date(2026, 1, 7),
        "record_date": date(2026, 1, 9),
        "effective_date": date(2026, 1, 17),
        "action_type": "rights_issue",
        "status": "completed",
        "source": "mock",
        "ratio": 0.5,
        "price": 90.0,
    }
    row.update(overrides)
    return row


def _check_names(findings: list[QualityFinding]) -> list[str]:
    return [finding.check_name for finding in findings]


class CorporateActionLifecycleTests(unittest.TestCase):
    """Verify lifecycle date completeness and ordering (SP 1.93)."""

    def test_complete_hk_action_has_no_findings(self) -> None:
        self.assertEqual(
            find_incomplete_corporate_actions(MarketTarget.HK, [_action("0700.HK")]), []
        )

    def test_complete_us_split_has_no_findings(self) -> None:
        row = _action("AAPL", market="US", action_type="split", ratio=2.0, price=None)
        self.assertEqual(find_incomplete_corporate_actions(MarketTarget.US, [row]), [])

    def test_missing_announce_date_is_flagged(self) -> None:
        row = _action("0700.HK", announce_date=None)
        findings = find_incomplete_corporate_actions(MarketTarget.HK, [row])
        self.assertIn("corporate_action_date_missing", _check_names(findings))
        self.assertEqual(findings[0].severity, "warning")
        self.assertIn("announce_date", findings[0].details)

    def test_ex_date_before_announcement_is_flagged(self) -> None:
        row = _action("0700.HK", ex_date=date(2026, 1, 1), announce_date=date(2026, 1, 7))
        findings = find_incomplete_corporate_actions(MarketTarget.HK, [row])
        self.assertIn("corporate_action_date_order", _check_names(findings))
        self.assertEqual(findings[0].severity, "error")

    def test_record_date_before_ex_date_is_flagged(self) -> None:
        row = _action("0700.HK", record_date=date(2026, 1, 1))
        findings = find_incomplete_corporate_actions(MarketTarget.HK, [row])
        self.assertIn("corporate_action_date_order", _check_names(findings))

    def test_effective_date_before_record_date_is_flagged(self) -> None:
        row = _action("0700.HK", effective_date=date(2026, 1, 5))
        findings = find_incomplete_corporate_actions(MarketTarget.HK, [row])
        self.assertIn("corporate_action_date_order", _check_names(findings))


class CorporateActionTermsTests(unittest.TestCase):
    """Verify terms completeness (SP 1.93)."""

    def test_split_missing_ratio_is_flagged(self) -> None:
        row = _action("AAPL", market="US", action_type="split", ratio=None)
        findings = find_incomplete_corporate_actions(MarketTarget.US, [row])
        self.assertIn("corporate_action_terms_incomplete", _check_names(findings))
        self.assertEqual(findings[-1].severity, "warning")
        self.assertIn("ratio", findings[-1].details)

    def test_dividend_missing_price_is_flagged(self) -> None:
        row = _action("0001.HK", action_type="dividend", ratio=None, price=None)
        findings = find_incomplete_corporate_actions(MarketTarget.HK, [row])
        self.assertIn("corporate_action_terms_incomplete", _check_names(findings))
        self.assertIn("price", findings[-1].details)

    def test_rights_issue_missing_price_is_flagged(self) -> None:
        row = _action("0700.HK", ratio=0.5, price=None)
        findings = find_incomplete_corporate_actions(MarketTarget.HK, [row])
        self.assertIn("corporate_action_terms_incomplete", _check_names(findings))
        self.assertIn("price", findings[-1].details)

    def test_non_positive_ratio_is_flagged(self) -> None:
        row = _action("AAPL", market="US", action_type="split", ratio=0.0)
        findings = find_incomplete_corporate_actions(MarketTarget.US, [row])
        self.assertIn("corporate_action_terms_incomplete", _check_names(findings))

    def test_unknown_action_type_skips_terms_check(self) -> None:
        row = _action("0700.HK", action_type="buyback", ratio=None, price=None)
        findings = find_incomplete_corporate_actions(MarketTarget.HK, [row])
        self.assertNotIn("corporate_action_terms_incomplete", _check_names(findings))
