"""Data-quality thresholds and failure policy for Harbor.

Each quality check is associated with warning and error limits. A check whose
error count exceeds its error limit fails; one whose warning count exceeds its
warning limit warns. The run-level decision stops (fails) when any check fails,
and otherwise continues, reporting whether warnings were raised. Thresholds
cover the checks introduced in SP 1.85-1.94.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from harbor.config import MarketTarget
from harbor.core.validation import QualityFinding


class CheckStatus(StrEnum):
    """The outcome of a single quality check."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class RunStatus(StrEnum):
    """The outcome of a quality run."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class QualityThreshold:
    """Warning and error limits for a single check."""

    check_name: str
    error_limit: int = 0
    warning_limit: int = 0


_DEFAULT_THRESHOLD = QualityThreshold("__default__", error_limit=0, warning_limit=0)

_DEFAULT_POLICY: dict[str, QualityThreshold] = {
    # Strict checks: any occurrence fails the run.
    "daily_quote_duplicate": QualityThreshold("daily_quote_duplicate"),
    "ohlc_invalid": QualityThreshold("ohlc_invalid"),
    "coverage_gap": QualityThreshold("coverage_gap"),
    "dividend_amount_invalid": QualityThreshold("dividend_amount_invalid"),
    "dividend_date_invalid": QualityThreshold("dividend_date_invalid"),
    "corporate_action_date_order": QualityThreshold("corporate_action_date_order"),
    "adjusted_factor_mismatch": QualityThreshold("adjusted_factor_mismatch"),
    "equity_event_mismatch": QualityThreshold("equity_event_mismatch"),
    # Tolerant checks: a small number of warnings is acceptable.
    "daily_quote_gap": QualityThreshold("daily_quote_gap", warning_limit=5),
    "abnormal_price_move": QualityThreshold("abnormal_price_move", warning_limit=3),
    "stale_quote": QualityThreshold("stale_quote", warning_limit=3),
    "special_flag_unreasonable": QualityThreshold("special_flag_unreasonable", warning_limit=3),
    "financials_incomplete": QualityThreshold(
        "financials_incomplete", error_limit=5, warning_limit=10
    ),
    "corporate_action_date_missing": QualityThreshold(
        "corporate_action_date_missing", warning_limit=3
    ),
    "corporate_action_terms_incomplete": QualityThreshold(
        "corporate_action_terms_incomplete", warning_limit=3
    ),
}


def build_policy(
    thresholds: Mapping[str, QualityThreshold] | None = None,
) -> dict[str, QualityThreshold]:
    """Return the default policy, optionally overridden by custom thresholds."""
    policy = dict(_DEFAULT_POLICY)
    if thresholds is not None:
        policy.update(thresholds)
    return policy


def evaluate_check(
    findings: Sequence[QualityFinding],
    threshold: QualityThreshold,
) -> CheckStatus:
    """Evaluate a set of findings for one check against its threshold.

    Errors dominate: the check fails when its error count exceeds the error
    limit, regardless of warnings. Otherwise it warns when warnings exceed the
    warning limit, and passes when both are within limits.
    """
    error_count = sum(1 for finding in findings if finding.severity == "error")
    warning_count = sum(1 for finding in findings if finding.severity == "warning")
    if error_count > threshold.error_limit:
        return CheckStatus.FAIL
    if warning_count > threshold.warning_limit:
        return CheckStatus.WARN
    return CheckStatus.PASS


def evaluate_run(
    market: MarketTarget,
    findings: Sequence[QualityFinding],
    thresholds: Mapping[str, QualityThreshold] | None = None,
) -> dict[str, object]:
    """Evaluate a set of findings against the policy and decide stop/continue.

    Args:
        market: The market the findings belong to.
        findings: The data-quality findings gathered for the run.
        thresholds: Optional per-check threshold overrides.

    Returns:
        A report with the run ``status`` (``pass``/``warn``/``fail``), the
        ``action`` (``continue`` unless any check fails, then ``stop``), the
        total finding count, the lists of failed and warned checks, and the
        per-check status map.
    """
    policy = build_policy(thresholds)
    by_check: dict[str, list[QualityFinding]] = {}
    for finding in findings:
        by_check.setdefault(finding.check_name, []).append(finding)

    check_results: dict[str, str] = {}
    failed_checks: list[str] = []
    warned_checks: list[str] = []
    for check_name, check_findings in sorted(by_check.items()):
        threshold = policy.get(check_name, _DEFAULT_THRESHOLD)
        status = evaluate_check(check_findings, threshold)
        check_results[check_name] = status.value
        if status is CheckStatus.FAIL:
            failed_checks.append(check_name)
        elif status is CheckStatus.WARN:
            warned_checks.append(check_name)

    if failed_checks:
        run_status = RunStatus.FAIL
    elif warned_checks:
        run_status = RunStatus.WARN
    else:
        run_status = RunStatus.PASS

    return {
        "market": market.value,
        "status": run_status.value,
        "action": "stop" if run_status is RunStatus.FAIL else "continue",
        "total_findings": len(findings),
        "failed_checks": failed_checks,
        "warned_checks": warned_checks,
        "checks": check_results,
    }
