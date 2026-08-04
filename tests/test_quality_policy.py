"""Data-quality threshold and failure policy tests."""

import unittest

from harbor.config import MarketTarget
from harbor.core.quality_policy import (
    CheckStatus,
    QualityThreshold,
    build_policy,
    evaluate_check,
    evaluate_run,
)
from harbor.core.validation import QualityFinding


def _error(check_name: str, symbol: str = "AAPL") -> QualityFinding:
    return QualityFinding(check_name, "error", symbol, "error detail")


def _warning(check_name: str, symbol: str = "AAPL") -> QualityFinding:
    return QualityFinding(check_name, "warning", symbol, "warning detail")


class EvaluateCheckTests(unittest.TestCase):
    """Verify per-check threshold evaluation."""

    def test_no_findings_is_pass(self) -> None:
        threshold = QualityThreshold("daily_quote_gap", warning_limit=5)
        self.assertEqual(evaluate_check([], threshold), CheckStatus.PASS)

    def test_warnings_within_limit_is_pass(self) -> None:
        threshold = QualityThreshold("daily_quote_gap", warning_limit=5)
        findings = [_warning("daily_quote_gap")] * 3
        self.assertEqual(evaluate_check(findings, threshold), CheckStatus.PASS)

    def test_warnings_over_limit_is_warn(self) -> None:
        threshold = QualityThreshold("daily_quote_gap", warning_limit=5)
        findings = [_warning("daily_quote_gap")] * 6
        self.assertEqual(evaluate_check(findings, threshold), CheckStatus.WARN)

    def test_error_over_limit_is_fail(self) -> None:
        threshold = QualityThreshold("daily_quote_duplicate")
        self.assertEqual(
            evaluate_check([_error("daily_quote_duplicate")], threshold), CheckStatus.FAIL
        )

    def test_error_dominates_warning(self) -> None:
        threshold = QualityThreshold("daily_quote_duplicate", warning_limit=5)
        findings = [_error("daily_quote_duplicate"), _warning("daily_quote_duplicate")]
        self.assertEqual(evaluate_check(findings, threshold), CheckStatus.FAIL)


class EvaluateRunTests(unittest.TestCase):
    """Verify run-level stop/continue decisions."""

    def test_empty_findings_pass_and_continue(self) -> None:
        report = evaluate_run(MarketTarget.US, [])
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["action"], "continue")
        self.assertEqual(report["total_findings"], 0)

    def test_warnings_within_limit_pass_and_continue(self) -> None:
        findings = [_warning("daily_quote_gap")] * 2
        report = evaluate_run(MarketTarget.US, findings)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["action"], "continue")

    def test_warnings_over_limit_warn_and_continue(self) -> None:
        findings = [_warning("daily_quote_gap")] * 6
        report = evaluate_run(MarketTarget.US, findings)
        self.assertEqual(report["status"], "warn")
        self.assertEqual(report["action"], "continue")
        self.assertIn("daily_quote_gap", report["warned_checks"])
        self.assertEqual(report["checks"]["daily_quote_gap"], "warn")

    def test_error_fails_and_stops(self) -> None:
        findings = [_error("daily_quote_duplicate")]
        report = evaluate_run(MarketTarget.US, findings)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["action"], "stop")
        self.assertIn("daily_quote_duplicate", report["failed_checks"])
        self.assertEqual(report["checks"]["daily_quote_duplicate"], "fail")

    def test_unknown_check_is_strict_by_default(self) -> None:
        report = evaluate_run(MarketTarget.HK, [_error("mystery_check")])
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["action"], "stop")

    def test_custom_policy_overrides_default(self) -> None:
        policy = {"daily_quote_duplicate": QualityThreshold("daily_quote_duplicate", error_limit=2)}
        findings = [_error("daily_quote_duplicate"), _error("daily_quote_duplicate")]
        report = evaluate_run(MarketTarget.US, findings, thresholds=policy)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["action"], "continue")

    def test_market_is_reported(self) -> None:
        report = evaluate_run(MarketTarget.HK, [])
        self.assertEqual(report["market"], "HK")


class BuildPolicyTests(unittest.TestCase):
    """Verify policy construction."""

    def test_default_policy_has_expected_checks(self) -> None:
        policy = build_policy()
        self.assertEqual(policy["daily_quote_duplicate"].error_limit, 0)
        self.assertEqual(policy["daily_quote_gap"].warning_limit, 5)
        self.assertEqual(policy["financials_incomplete"].error_limit, 5)

    def test_custom_policy_overrides_defaults(self) -> None:
        policy = build_policy(
            {"daily_quote_gap": QualityThreshold("daily_quote_gap", warning_limit=10)}
        )
        self.assertEqual(policy["daily_quote_gap"].warning_limit, 10)
