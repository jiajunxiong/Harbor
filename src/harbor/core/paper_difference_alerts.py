"""Difference thresholds, alerts and coverage (MVP 4 / SP 4.73 / 4.74).

Checks the quantified differences (SP 4.71) against pre-registered thresholds
and records every exceedance as an alert — never silently ignored (阈值与告警,
SP 4.73). When the difference returns within the threshold, the alert is no
longer emitted (recovery). Coverage gaps and missing data are annotated
separately so the paper loop never draws a conclusion from insufficient
evidence (覆盖不足告警, SP 4.74).

The alert fingerprint is stable and excludes the paper run id, so the same
inputs replay to the same alerts (SP 4.81).

Pure core logic: depends on the difference metrics report; never touches
storage or CLI code.
"""

import hashlib
import json
from dataclasses import dataclass

from harbor.core.paper_difference_metrics import (
    DifferenceKind,
    DifferenceMetric,
    DifferenceMetricsReport,
)


class DifferenceAlertError(ValueError):
    """Raised when a difference alert is invalid (SP 4.73)."""


@dataclass(frozen=True)
class PreRegisteredThreshold:
    """A pre-registered difference threshold for one kind (SP 4.73)."""

    kind: DifferenceKind
    threshold: float

    def __post_init__(self) -> None:
        if self.threshold < 0:
            raise DifferenceAlertError("threshold must be non-negative.")


@dataclass(frozen=True)
class DifferenceAlert:
    """A recorded difference alert (SP 4.73)."""

    paper_run_id: str
    metric: DifferenceMetric
    threshold: float
    message: str

    def __post_init__(self) -> None:
        if not self.message:
            raise DifferenceAlertError("message must be non-empty.")

    def readable(self) -> str:
        """Render the alert as a compact summary."""
        return f"[{self.metric.kind.value}] {self.message}"


def _threshold_for(
    thresholds: tuple[PreRegisteredThreshold, ...], kind: DifferenceKind
) -> PreRegisteredThreshold | None:
    """Return the registered threshold for ``kind`` or ``None`` when absent."""
    for threshold in thresholds:
        if threshold.kind is kind:
            return threshold
    return None


def check_difference_thresholds(
    *,
    paper_run_id: str,
    report: DifferenceMetricsReport,
    thresholds: tuple[PreRegisteredThreshold, ...],
) -> tuple[DifferenceAlert, ...]:
    """Record an alert for every difference beyond its threshold (SP 4.73).

    A kind without a registered threshold is skipped (no pre-registered
    baseline to judge against). A difference whose absolute value exceeds the
    threshold is recorded; a later run within the threshold emits no alert
    (recovery, SP 4.79).
    """
    alerts: list[DifferenceAlert] = []
    for metric in report.metrics:
        threshold = _threshold_for(thresholds, metric.kind)
        if threshold is None:
            continue
        if abs(metric.difference) <= threshold.threshold:
            continue
        direction = "above" if metric.difference > 0 else "below"
        message = (
            f"{metric.market.value}/{metric.symbol} on "
            f"{metric.rebalance_date.isoformat()}: {metric.kind.value} "
            f"{metric.difference:+.4f} {direction} registered threshold "
            f"{threshold.threshold:.4f} (assumed {metric.assumed:.4f}, actual "
            f"{metric.actual:.4f})."
        )
        alerts.append(
            DifferenceAlert(
                paper_run_id=paper_run_id,
                metric=metric,
                threshold=threshold.threshold,
                message=message,
            )
        )
    return tuple(alerts)


def difference_alerts_fingerprint(alerts: tuple[DifferenceAlert, ...]) -> str:
    """Return the stable SHA-256 fingerprint of an alert set (SP 4.73).

    The paper run id is excluded so the same differences replay to the same
    alerts (SP 4.81).
    """
    payload = [
        {
            "market": alert.metric.market.value,
            "rebalance_date": alert.metric.rebalance_date.isoformat(),
            "symbol": alert.metric.symbol,
            "kind": alert.metric.kind.value,
            "threshold": alert.threshold,
            "message": alert.message,
        }
        for alert in alerts
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CoverageAssessment:
    """The paper coverage of one market (SP 4.74)."""

    paper_run_id: str
    market: str
    filled_days: int
    expected_days: int
    coverage_pct: float
    missing_data: tuple[str, ...] = ()
    sufficient: bool = True
    notes: tuple[str, ...] = ()

    def readable(self) -> str:
        """Render the coverage as a compact summary."""
        status = "sufficient" if self.sufficient else "insufficient"
        lines = [
            f"{self.market} coverage {self.coverage_pct:.1%} "
            f"({self.filled_days}/{self.expected_days} days): {status}"
        ]
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def assess_paper_coverage(
    *,
    paper_run_id: str,
    market: str,
    filled_days: int,
    expected_days: int,
    missing_data: tuple[str, ...] = (),
    min_coverage_pct: float = 1.0,
) -> CoverageAssessment:
    """Assess whether a market's paper coverage is sufficient (SP 4.74).

    Coverage is the filled-day share of the expected trading days. Missing
    data is annotated and makes the coverage insufficient regardless of the
    ratio — the loop never concludes from incomplete evidence.
    """
    if not paper_run_id:
        raise DifferenceAlertError("paper_run_id must be non-empty.")
    if filled_days < 0 or expected_days <= 0:
        raise DifferenceAlertError("filled_days must be non-negative and expected_days positive.")
    if filled_days > expected_days:
        raise DifferenceAlertError("filled_days cannot exceed expected_days.")
    if not 0 <= min_coverage_pct <= 1:
        raise DifferenceAlertError("min_coverage_pct must be in [0, 1].")
    coverage = filled_days / expected_days
    notes: list[str] = []
    if coverage < min_coverage_pct:
        notes.append(f"coverage {coverage:.1%} below the {min_coverage_pct:.0%} minimum.")
    if missing_data:
        notes.append("missing data annotated; no conclusion drawn from incomplete evidence.")
    sufficient = coverage >= min_coverage_pct and not missing_data
    return CoverageAssessment(
        paper_run_id=paper_run_id,
        market=market,
        filled_days=filled_days,
        expected_days=expected_days,
        coverage_pct=coverage,
        missing_data=missing_data,
        sufficient=sufficient,
        notes=tuple(notes),
    )


def coverage_insufficient_alerts(
    assessments: tuple[CoverageAssessment, ...],
) -> tuple[str, ...]:
    """Return an alert string per insufficient-coverage market (SP 4.74)."""
    return tuple(
        f"coverage insufficient for {assessment.market}: {assessment.coverage_pct:.1%} covered"
        for assessment in assessments
        if not assessment.sufficient
    )
