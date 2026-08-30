"""Circuit-breaker recovery flow for the paper loop (MVP 4 / SP 4.42).

A run can recover from a circuit break or freeze only after an independent
review passes; the recovery review and its approval are archived together so
the recovery is fully auditable (熔断恢复流程). The recovery never happens
silently: :func:`require_recovery_review` refuses a failed or missing review.

Pure core logic: depends on the paper domain (``RiskApproval``) and the
circuit-breaker assessment (SP 4.37 / 4.38); never touches storage or CLI
code.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from harbor.core.paper_circuit_breakers import BreakerAssessment
from harbor.core.paper_domain import RiskApproval


class PaperRecoveryError(ValueError):
    """Raised when a recovery cannot be archived (SP 4.42)."""


def _now_utc() -> datetime:
    """Return the current UTC time (default recovery timestamp)."""
    return datetime.now(timezone.utc)


def _require_utc_aware(timestamp: datetime, what: str) -> None:
    """Require an explicit UTC offset so timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError(f"{what} must be UTC-aware (offset 0).")


@dataclass(frozen=True)
class RecoveryReview:
    """An independent review of a circuit break before recovery (SP 4.42)."""

    paper_run_id: str
    reviewer: str
    reviewed_at: datetime
    passed: bool
    notes: str

    def __post_init__(self) -> None:
        if not self.paper_run_id or not self.reviewer:
            raise PaperRecoveryError("Recovery review ids must be non-empty.")
        if not self.passed and not self.notes:
            raise PaperRecoveryError("A failed recovery review must record notes.")
        _require_utc_aware(self.reviewed_at, "Review time")

    def readable(self) -> str:
        """Render the review as a compact summary."""
        status = "PASSED" if self.passed else "FAILED"
        return (
            f"recovery review {status} by {self.reviewer} at "
            f"{self.reviewed_at.isoformat()}: {self.notes}"
        )


@dataclass(frozen=True)
class RecoveryRecord:
    """The archived recovery review and approval (SP 4.42)."""

    paper_run_id: str
    review: RecoveryReview
    approval: RiskApproval
    recovered_at: datetime

    def __post_init__(self) -> None:
        if self.review.paper_run_id != self.paper_run_id:
            raise PaperRecoveryError("Recovery review must match the paper run.")
        if not self.review.passed:
            raise PaperRecoveryError("Recovery requires a passed independent review.")
        if self.approval.decision.value != "APPROVED":
            raise PaperRecoveryError("Recovery requires an approved recovery approval.")
        _require_utc_aware(self.recovered_at, "Recovery time")

    def readable(self) -> str:
        """Render the archived record as a compact summary."""
        return (
            f"recovery archived for {self.paper_run_id} at "
            f"{self.recovered_at.isoformat()}\n  {self.review.readable()}\n  "
            f"{self.approval.readable()}"
        )


def recovery_review_required(assessment: BreakerAssessment) -> bool:
    """Whether a breaker assessment requires an independent review (SP 4.42).

    A triggered monthly breaker or a triggered daily breaker requires an
    independent review before recovery.
    """
    return assessment.triggered and assessment.requires_review


def require_recovery_review(review: RecoveryReview) -> None:
    """Raise unless the independent review passed (SP 4.42).

    Raises:
        PaperRecoveryError: If the review did not pass.
    """
    if not review.passed:
        raise PaperRecoveryError(
            "Recovery requires a passed independent review; review did not pass."
        )


def recover_run(
    *,
    review: RecoveryReview,
    approval: RiskApproval,
    recovered_at: datetime | None = None,
) -> RecoveryRecord:
    """Archive a recovery after a passed independent review (SP 4.42).

    Raises:
        PaperRecoveryError: If the review did not pass, the approval is not
            APPROVED, or the timestamps are not UTC-aware.
    """
    require_recovery_review(review)
    timestamp = _now_utc() if recovered_at is None else recovered_at
    _require_utc_aware(timestamp, "Recovery time")
    return RecoveryRecord(
        paper_run_id=review.paper_run_id,
        review=review,
        approval=approval,
        recovered_at=timestamp,
    )
