"""Per-run data-quality reporting for Harbor (per market).

A quality run persists its findings to the ``quality_issues`` table and renders
a JSON summary for the market. The summary reports the finding counts, the
per-check breakdown, and the stop/continue decision from the quality policy.
"""

import json
from collections import Counter
from collections.abc import Mapping, Sequence

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
