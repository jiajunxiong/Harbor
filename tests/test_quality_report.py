"""Per-run quality report tests."""

import json
import unittest

from harbor.config import MarketTarget
from harbor.core.quality_report import (
    build_quality_summary,
    generate_quality_report,
    persist_quality_issues,
    render_quality_csv,
    render_quality_report,
    summarize_quality_issues,
)
from harbor.core.validation import QualityFinding


class FakeRepository:
    """A stand-in repository that records quality issues in memory."""

    def __init__(self) -> None:
        self.issues: list[dict[str, object]] = []

    def record_quality_issue(
        self,
        market: str,
        run_id: str,
        check_name: str,
        severity: str,
        symbol: str | None = None,
        details: str | None = None,
    ) -> int:
        self.issues.append(
            {
                "market": market,
                "run_id": run_id,
                "check_name": check_name,
                "severity": severity,
                "symbol": symbol,
                "details": details,
            }
        )
        return 1


class PersistQualityIssuesTests(unittest.TestCase):
    """Verify findings are written to the quality-issues table."""

    def test_persist_writes_each_finding(self) -> None:
        repository = FakeRepository()
        findings = [
            QualityFinding("daily_quote_duplicate", "error", "AAPL", "2 records."),
            QualityFinding("daily_quote_gap", "warning", "AAPL", "Missing 1 day."),
        ]
        count = persist_quality_issues(repository, MarketTarget.US, "run-1", findings)
        self.assertEqual(count, 2)
        self.assertEqual(len(repository.issues), 2)
        first = repository.issues[0]
        self.assertEqual(first["market"], "US")
        self.assertEqual(first["run_id"], "run-1")
        self.assertEqual(first["check_name"], "daily_quote_duplicate")
        self.assertEqual(first["severity"], "error")
        self.assertEqual(first["symbol"], "AAPL")
        self.assertEqual(first["details"], "2 records.")

    def test_persist_empty_findings_writes_nothing(self) -> None:
        repository = FakeRepository()
        count = persist_quality_issues(repository, MarketTarget.HK, "run-2", [])
        self.assertEqual(count, 0)
        self.assertEqual(repository.issues, [])


class BuildQualitySummaryTests(unittest.TestCase):
    """Verify the JSON summary shape."""

    def test_summary_counts_by_severity_and_check(self) -> None:
        findings = [
            QualityFinding("daily_quote_duplicate", "error", "AAPL"),
            QualityFinding("daily_quote_gap", "warning", "AAPL"),
            QualityFinding("daily_quote_gap", "warning", "MSFT"),
        ]
        summary = build_quality_summary(MarketTarget.US, "run-1", findings)
        self.assertEqual(summary["run_id"], "run-1")
        self.assertEqual(summary["market"], "US")
        self.assertEqual(summary["total_findings"], 3)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["warnings"], 2)
        self.assertEqual(
            summary["findings_by_check"],
            {"daily_quote_duplicate": 1, "daily_quote_gap": 2},
        )
        self.assertNotIn("status", summary)

    def test_summary_includes_decision(self) -> None:
        decision = {
            "status": "fail",
            "action": "stop",
            "failed_checks": ["daily_quote_duplicate"],
            "warned_checks": [],
        }
        summary = build_quality_summary(MarketTarget.US, "run-1", [], decision=decision)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["action"], "stop")
        self.assertEqual(summary["failed_checks"], ["daily_quote_duplicate"])


class GenerateQualityReportTests(unittest.TestCase):
    """Verify the end-to-end report generation."""

    def test_failing_run_stops_and_persists(self) -> None:
        repository = FakeRepository()
        findings = [QualityFinding("daily_quote_duplicate", "error", "AAPL")]
        report = generate_quality_report(repository, MarketTarget.US, "run-1", findings)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["action"], "stop")
        self.assertEqual(report["records_written"], 1)
        self.assertEqual(report["total_findings"], 1)
        self.assertEqual(report["errors"], 1)
        self.assertEqual(repository.issues[0]["run_id"], "run-1")

    def test_clean_run_passes_and_continues(self) -> None:
        repository = FakeRepository()
        report = generate_quality_report(repository, MarketTarget.HK, "run-2", [])
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["action"], "continue")
        self.assertEqual(report["records_written"], 0)

    def test_render_quality_report_round_trips(self) -> None:
        repository = FakeRepository()
        findings = [QualityFinding("stale_quote", "warning", "AAPL")]
        text = render_quality_report(repository, MarketTarget.US, "run-3", findings)
        self.assertEqual(
            json.loads(text),
            generate_quality_report(repository, MarketTarget.US, "run-3", findings),
        )


class SummarizeQualityIssuesTests(unittest.TestCase):
    """Verify materialized quality-issue summary construction."""

    def test_summary_counts_and_policy_decision(self) -> None:
        issues = [
            {
                "run_id": "run-1",
                "market": "US",
                "symbol": "AAPL",
                "check_name": "daily_quote_duplicate",
                "severity": "error",
                "details": "2 records.",
                "resolved": False,
            },
            {
                "run_id": "run-1",
                "market": "US",
                "symbol": "MSFT",
                "check_name": "daily_quote_gap",
                "severity": "warning",
                "details": "Missing 1.",
                "resolved": True,
            },
        ]
        summary = summarize_quality_issues(MarketTarget.US, issues)
        self.assertEqual(summary["market"], "US")
        self.assertEqual(summary["total_findings"], 2)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["warnings"], 1)
        self.assertEqual(summary["unresolved"], 1)
        self.assertEqual(
            summary["findings_by_check"],
            {"daily_quote_duplicate": 1, "daily_quote_gap": 1},
        )
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["action"], "stop")

    def test_empty_issues_summary(self) -> None:
        summary = summarize_quality_issues(MarketTarget.HK, [])
        self.assertEqual(summary["total_findings"], 0)
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(summary["action"], "continue")


class RenderQualityCsvTests(unittest.TestCase):
    """Verify CSV rendering of quality issues."""

    def test_csv_has_header_and_row_data(self) -> None:
        issues = [
            {
                "run_id": "run-1",
                "market": "US",
                "symbol": "AAPL",
                "check_name": "stale_quote",
                "severity": "warning",
                "details": "5 days.",
                "resolved": False,
            }
        ]
        text = render_quality_csv(issues)
        lines = text.strip().splitlines()
        self.assertEqual(
            lines[0],
            "run_id,market,symbol,check_name,severity,details,resolved",
        )
        self.assertIn("AAPL", text)
        self.assertIn("stale_quote", text)
        self.assertIn("False", text)

    def test_csv_handles_missing_columns(self) -> None:
        text = render_quality_csv([{"check_name": "coverage_gap"}])
        self.assertIn("coverage_gap", text)
        self.assertNotIn("None", text)
