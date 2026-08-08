"""Data quality correlation tests (MVP 2 / SP 2.65).

Verifies that MVP 1 quality issues (SP 1.28) and SP 2.13 precheck findings are
filtered to the markets and symbols a backtest actually used, that resolved
issues are counted separately, and that the correlation renders into the HTML
report's data-quality section (数据质量关联).
"""

import unittest
from typing import Any

from harbor.core.backtest_domain import Market
from harbor.core.data_readiness import PrecheckFinding, PrecheckReport, PrecheckSeverity
from harbor.core.html_report import render_html_report
from harbor.core.quality_correlation import (
    CorrelationError,
    QualityCorrelationReport,
    QualityIssue,
    QualitySource,
    correlate_quality,
)

HK = Market.HK
US = Market.US


def _issue(
    *,
    market: Market = HK,
    symbol: str | None = None,
    check_name: str = "gap_check",
    severity: PrecheckSeverity = PrecheckSeverity.WARNING,
    details: str = "gap on 2026-03-02",
    resolved: bool = False,
) -> QualityIssue:
    return QualityIssue(
        market=market,
        symbol=symbol,
        check_name=check_name,
        severity=severity,
        details=details,
        resolved=resolved,
    )


def _finding(
    severity: PrecheckSeverity = PrecheckSeverity.WARNING,
    scope: str = "HK",
    message: str = "survivorship-bias risk: unknown history",
) -> PrecheckFinding:
    return PrecheckFinding(severity, scope, message)


def _precheck(*findings: PrecheckFinding) -> PrecheckReport:
    return PrecheckReport(findings)


def _correlate(
    *,
    issues: tuple[QualityIssue, ...] = (),
    findings: tuple[PrecheckFinding, ...] = (),
    markets: tuple[Market, ...] = (HK,),
    symbols: dict[Market, tuple[str, ...]] | None = None,
) -> QualityCorrelationReport:
    return correlate_quality(
        precheck=_precheck(*findings),
        issues=issues,
        markets=markets,
        symbols=symbols,
    )


class QualityIssueValidationTests(unittest.TestCase):
    """Verify the MVP 1 quality-issue record validation."""

    def test_empty_check_name_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "check_name"):
            QualityIssue(market=HK, check_name="", severity=PrecheckSeverity.WARNING, details="d")

    def test_empty_symbol_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "symbol"):
            QualityIssue(
                market=HK, symbol="", check_name="c", severity=PrecheckSeverity.WARNING, details="d"
            )

    def test_market_scoped_and_symbol_scoped_ok(self) -> None:
        market_issue = _issue(market=HK)
        symbol_issue = _issue(market=HK, symbol="0001.HK")
        self.assertIsNone(market_issue.symbol)
        self.assertEqual(symbol_issue.symbol, "0001.HK")


class CorrelateQualityTests(unittest.TestCase):
    """Verify the correlation filters findings to the data actually used."""

    def test_market_level_issue_included_for_used_market(self) -> None:
        report = _correlate(issues=(_issue(market=HK),))
        self.assertEqual(report.unresolved_count, 1)
        self.assertEqual(report.precheck_warning_count, 0)
        self.assertEqual(report.findings[0].source, QualitySource.QUALITY_ISSUE)
        self.assertEqual(report.findings[0].scope, "HK")
        self.assertEqual(report.findings[0].check_name, "gap_check")

    def test_symbol_issue_included_only_when_symbol_used(self) -> None:
        report = _correlate(
            issues=(_issue(market=HK, symbol="0001.HK"), _issue(market=HK, symbol="0002.HK")),
            symbols={HK: ("0001.HK",)},
        )
        self.assertEqual(report.unresolved_count, 1)
        self.assertEqual(report.findings[0].scope, "HK/0001.HK")

    def test_issue_for_unused_market_excluded(self) -> None:
        report = _correlate(
            issues=(_issue(market=US, symbol="AAPL"),),
            markets=(HK,),
            symbols={HK: ("0001.HK",)},
        )
        self.assertEqual(report.unresolved_count, 0)
        self.assertFalse(report.has_findings)

    def test_resolved_issues_counted_but_not_findings(self) -> None:
        report = _correlate(issues=(_issue(market=HK, resolved=True),))
        self.assertEqual(report.resolved_count, 1)
        self.assertEqual(report.unresolved_count, 0)
        self.assertFalse(report.has_findings)

    def test_precheck_findings_merged(self) -> None:
        report = _correlate(
            issues=(_issue(market=HK),),
            findings=(
                _finding(PrecheckSeverity.WARNING, "HK", "survivorship-bias risk: unknown history"),
                _finding(PrecheckSeverity.WARNING, "HK/fx", "missing FX"),
            ),
        )
        self.assertEqual(report.unresolved_count, 1)
        self.assertEqual(report.precheck_warning_count, 2)
        precheck = [f for f in report.findings if f.source is QualitySource.PRECHECK]
        self.assertEqual(len(precheck), 2)
        self.assertEqual(precheck[0].scope, "HK")
        self.assertEqual(precheck[0].check_name, "precheck")
        self.assertEqual(precheck[1].scope, "HK/fx")

    def test_precheck_errors_are_kept(self) -> None:
        report = _correlate(findings=(_finding(PrecheckSeverity.ERROR, "HK", "no trading days"),))
        self.assertEqual(report.precheck_error_count, 1)
        self.assertTrue(report.has_findings)
        self.assertEqual(report.findings[0].severity, PrecheckSeverity.ERROR)

    def test_empty_correlation_has_no_findings(self) -> None:
        report = _correlate()
        self.assertFalse(report.has_findings)
        self.assertEqual(report.unresolved_count, 0)
        self.assertEqual(report.resolved_count, 0)
        self.assertIn("no data-quality findings", report.readable())

    def test_markets_deduplicated(self) -> None:
        report = _correlate(markets=(HK, HK, US))
        self.assertEqual(report.markets, (HK, US))

    def test_readable_lists_findings_and_counts(self) -> None:
        report = _correlate(
            issues=(_issue(market=HK, symbol="0001.HK", check_name="ohlc_check"),),
            findings=(_finding(PrecheckSeverity.WARNING, "HK", "low volume"),),
        )
        text = report.readable()
        self.assertIn("markets used: HK", text)
        self.assertIn("unresolved MVP 1 quality issues: 1", text)
        self.assertIn("precheck warnings: 1", text)
        self.assertIn("HK/0001.HK", text)
        self.assertIn("ohlc_check", text)
        self.assertIn("low volume", text)

    def test_empty_markets_rejected(self) -> None:
        with self.assertRaisesRegex(CorrelationError, "At least one market"):
            correlate_quality(precheck=_precheck(), issues=(), markets=())

    def test_symbols_key_not_a_used_market_rejected(self) -> None:
        with self.assertRaisesRegex(CorrelationError, "not a used market"):
            correlate_quality(
                precheck=_precheck(),
                issues=(),
                markets=(HK,),
                symbols={US: ("AAPL",)},
            )

    def test_report_retains_precheck(self) -> None:
        precheck = _precheck(_finding())
        report = correlate_quality(precheck=precheck, issues=(), markets=(HK,))
        self.assertIs(report.precheck, precheck)


class HtmlQualitySectionTests(unittest.TestCase):
    """Verify the SP 2.65 quality section renders inside the HTML report."""

    def _artifact(self) -> dict[str, Any]:
        return {
            "run": {
                "run_id": "run-1",
                "status": "COMPLETED",
                "succeeded": True,
                "inputs": {
                    "code_version": "1.0.0",
                    "config_hash": "abc123",
                    "data_cutoff": "2026-06-30",
                    "data_range_start": "2026-01-02",
                    "data_range_end": "2026-06-30",
                },
                "base_currency": "HKD",
                "initial_capital": 100_000.0,
                "day_count": 2,
                "reconciliation_failures": [],
            },
            "config": {"strategy": "quarterly-low-vol", "markets": ["HK"]},
            "metrics": {"performance": None, "drawdown": None},
            "warnings": [],
            "net_values": [
                {"date": "2026-01-02", "total_value": 100_000.0},
                {"date": "2026-01-03", "total_value": 101_000.0},
            ],
        }

    def test_no_quality_section_by_default(self) -> None:
        text = render_html_report(self._artifact())
        self.assertNotIn("数据质量关联", text)

    def test_quality_section_renders(self) -> None:
        report = _correlate(
            issues=(_issue(market=HK, symbol="0001.HK", check_name="ohlc_check"),),
            findings=(_finding(PrecheckSeverity.WARNING, "HK", "low volume"),),
        )
        text = render_html_report(self._artifact(), quality=report)
        self.assertIn('<section id="quality">', text)
        self.assertIn("数据质量关联", text)
        self.assertIn("ohlc_check", text)
        self.assertIn("HK/0001.HK", text)
        self.assertIn("low volume", text)
        self.assertIn("precheck warnings) 1", text)

    def test_quality_details_are_html_escaped(self) -> None:
        report = _correlate(issues=(_issue(market=HK, details="<script>alert(1)</script>"),))
        text = render_html_report(self._artifact(), quality=report)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>alert(1)</script>", text)

    def test_empty_correlation_renders_note(self) -> None:
        report = _correlate()
        text = render_html_report(self._artifact(), quality=report)
        self.assertIn("no known quality findings for the data used", text)
        self.assertIn("unresolved) 0", text)


if __name__ == "__main__":
    unittest.main()
