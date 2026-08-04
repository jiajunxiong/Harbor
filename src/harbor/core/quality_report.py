"""Per-run data-quality reporting for Harbor (per market).

A quality run persists its findings to the ``quality_issues`` table and renders
a JSON summary for the market. The summary reports the finding counts, the
per-check breakdown, and the stop/continue decision from the quality policy.
"""

import csv
import io
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from harbor.config import MarketTarget
from harbor.core.quality_policy import QualityThreshold, evaluate_run
from harbor.core.validation import QualityFinding
from harbor.storage.repositories import Repository


def persist_quality_issues(
    repository: Repository,
    market: MarketTarget,
    run_id: str,
    findings: Sequence[QualityFinding],
) -> int:
    """Write findings to the quality-issues table.

    Args:
        repository: The storage repository used to record findings.
        market: The market the findings belong to.
        run_id: The ingestion run the findings are associated with.
        findings: The data-quality findings to persist.

    Returns:
        The number of quality-issue records written.
    """
    total = 0
    for finding in findings:
        total += repository.record_quality_issue(
            market.value,
            run_id,
            finding.check_name,
            finding.severity,
            symbol=finding.symbol,
            details=finding.details,
        )
    return total


def build_quality_summary(
    market: MarketTarget,
    run_id: str,
    findings: Sequence[QualityFinding],
    decision: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a JSON-serializable summary of a quality run.

    Args:
        market: The market the findings belong to.
        run_id: The ingestion run the findings are associated with.
        findings: The data-quality findings of the run.
        decision: An optional policy decision to embed in the summary.

    Returns:
        The summary with run context, finding counts, and (when provided) the
        policy status, action, and failed/warned check lists.
    """
    by_check = Counter(finding.check_name for finding in findings)
    summary: dict[str, object] = {
        "run_id": run_id,
        "market": market.value,
        "total_findings": len(findings),
        "errors": sum(1 for finding in findings if finding.severity == "error"),
        "warnings": sum(1 for finding in findings if finding.severity == "warning"),
        "findings_by_check": dict(sorted(by_check.items())),
    }
    if decision is not None:
        summary["status"] = decision.get("status")
        summary["action"] = decision.get("action")
        summary["failed_checks"] = decision.get("failed_checks")
        summary["warned_checks"] = decision.get("warned_checks")
    return summary


def generate_quality_report(
    repository: Repository,
    market: MarketTarget,
    run_id: str,
    findings: Sequence[QualityFinding],
    thresholds: Mapping[str, QualityThreshold] | None = None,
) -> dict[str, object]:
    """Persist findings, evaluate the policy, and return a JSON summary.

    Args:
        repository: The storage repository used to record findings.
        market: The market the findings belong to.
        run_id: The ingestion run the findings are associated with.
        findings: The data-quality findings of the run.
        thresholds: Optional per-check threshold overrides for the policy.

    Returns:
        The full per-run quality report summary, including the number of
        quality-issue records written.
    """
    records_written = persist_quality_issues(repository, market, run_id, findings)
    decision = evaluate_run(market, findings, thresholds)
    summary = build_quality_summary(market, run_id, findings, decision)
    summary["records_written"] = records_written
    return summary


def render_quality_report(
    repository: Repository,
    market: MarketTarget,
    run_id: str,
    findings: Sequence[QualityFinding],
    thresholds: Mapping[str, QualityThreshold] | None = None,
) -> str:
    """Render the per-run quality report as a JSON document."""
    return json.dumps(
        generate_quality_report(repository, market, run_id, findings, thresholds),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )


_CSV_COLUMNS = ("run_id", "market", "symbol", "check_name", "severity", "details", "resolved")


def summarize_quality_issues(
    market: MarketTarget,
    issues: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Summarize materialized quality-issue rows for a market.

    Args:
        market: The market the issues belong to.
        issues: Quality-issue rows as returned by
            :meth:`harbor.storage.repositories.Repository.fetch_quality_issues`.

    Returns:
        A JSON-serializable summary with finding counts, the unresolved count,
        the per-check breakdown, and the policy status/action.
    """
    findings = [
        QualityFinding(
            str(row.get("check_name", "")),
            str(row.get("severity", "")),
            str(row["symbol"]) if row.get("symbol") is not None else None,
            str(row["details"]) if row.get("details") is not None else None,
        )
        for row in issues
    ]
    decision = evaluate_run(market, findings)
    by_check = Counter(finding.check_name for finding in findings)
    return {
        "market": market.value,
        "total_findings": len(findings),
        "errors": sum(1 for finding in findings if finding.severity == "error"),
        "warnings": sum(1 for finding in findings if finding.severity == "warning"),
        "unresolved": sum(1 for row in issues if not row.get("resolved", False)),
        "findings_by_check": dict(sorted(by_check.items())),
        "status": decision.get("status"),
        "action": decision.get("action"),
    }


def render_quality_csv(issues: Sequence[Mapping[str, Any]]) -> str:
    """Render quality-issue rows as CSV text with a header row."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS)
    writer.writeheader()
    for row in issues:
        writer.writerow({column: row.get(column, "") for column in _CSV_COLUMNS})
    return buffer.getvalue()
