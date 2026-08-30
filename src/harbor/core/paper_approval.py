"""Human approval workflow for the paper loop (MVP 4 / SP 4.39).

High-risk orders and strategies require human approval; every approval record
carries the approver, the reason and the UTC decision time so manual
intervention is fully auditable (SP 4.7 / 4.39). NORMAL-risk requests do not
need approval; a HIGH-risk request is blocked until an APPROVED decision
exists (:meth:`ApprovalWorkflow.require_approved`).

Pure core logic: depends on the paper domain (``ApprovalDecision``) and never
touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from harbor.core.paper_domain import ApprovalDecision


class ApprovalScope(StrEnum):
    """What an approval request targets (SP 4.39)."""

    ORDER = "ORDER"
    RUN = "RUN"


class RiskLevel(StrEnum):
    """Whether a request is high-risk and therefore requires approval."""

    NORMAL = "NORMAL"
    HIGH = "HIGH"


class PaperApprovalError(ValueError):
    """Raised when an approval workflow is invalid (SP 4.39)."""


def _now_utc() -> datetime:
    """Return the current UTC time (default request/decision timestamp)."""
    return datetime.now(timezone.utc)


def _require_utc_aware(timestamp: datetime, what: str) -> None:
    """Require an explicit UTC offset so timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError(f"{what} must be UTC-aware (offset 0).")


@dataclass(frozen=True)
class ApprovalRequest:
    """A request for human approval (SP 4.39)."""

    request_id: str
    paper_run_id: str
    scope: ApprovalScope
    scope_id: str
    risk_level: RiskLevel
    reason: str
    requested_at: datetime

    def __post_init__(self) -> None:
        if not self.request_id or not self.paper_run_id or not self.scope_id:
            raise PaperApprovalError("Approval request ids must be non-empty.")
        if not self.reason:
            raise PaperApprovalError("Approval request reason must be non-empty.")
        _require_utc_aware(self.requested_at, "Request time")

    def readable(self) -> str:
        """Render the request as a compact summary."""
        return (
            f"approval request {self.request_id} {self.scope.value}:{self.scope_id} "
            f"{self.risk_level.value} ({self.reason}) at "
            f"{self.requested_at.isoformat()}"
        )


@dataclass(frozen=True)
class ApprovalDecisionRecord:
    """A recorded human approval decision (SP 4.39)."""

    request_id: str
    decision: ApprovalDecision
    approver: str
    reason: str
    decided_at: datetime

    def __post_init__(self) -> None:
        if not self.request_id or not self.approver:
            raise PaperApprovalError("Decision request id and approver must be non-empty.")
        if not self.reason:
            raise PaperApprovalError("Decision reason must be non-empty.")
        _require_utc_aware(self.decided_at, "Decision time")

    def readable(self) -> str:
        """Render the decision as a compact summary."""
        return (
            f"{self.decision.value} {self.request_id} by {self.approver} "
            f"({self.reason}) at {self.decided_at.isoformat()}"
        )


@dataclass(frozen=True)
class ApprovalWorkflow:
    """An immutable record of approval requests and decisions (SP 4.39)."""

    requests: tuple[ApprovalRequest, ...] = ()
    decisions: tuple[ApprovalDecisionRecord, ...] = ()

    def __post_init__(self) -> None:
        request_ids = [request.request_id for request in self.requests]
        if len(set(request_ids)) != len(request_ids):
            raise PaperApprovalError("Approval request ids must be unique.")
        if request_ids != sorted(request_ids):
            raise PaperApprovalError("Approval requests must be key-sorted.")

    def request(
        self,
        *,
        request_id: str,
        paper_run_id: str,
        scope: ApprovalScope,
        scope_id: str,
        risk_level: RiskLevel,
        reason: str,
        requested_at: datetime | None = None,
    ) -> "ApprovalWorkflow":
        """Return a new workflow with the request recorded (SP 4.39)."""
        timestamp = _now_utc() if requested_at is None else requested_at
        entry = ApprovalRequest(
            request_id=request_id,
            paper_run_id=paper_run_id,
            scope=scope,
            scope_id=scope_id,
            risk_level=risk_level,
            reason=reason,
            requested_at=timestamp,
        )
        merged = tuple(sorted((*self.requests, entry), key=lambda item: item.request_id))
        return ApprovalWorkflow(requests=merged, decisions=self.decisions)

    def decide(
        self,
        *,
        request_id: str,
        decision: ApprovalDecision,
        approver: str,
        reason: str,
        decided_at: datetime | None = None,
    ) -> "ApprovalWorkflow":
        """Return a new workflow with the decision recorded (SP 4.39)."""
        if not any(request.request_id == request_id for request in self.requests):
            raise PaperApprovalError(f"No approval request {request_id!r} is recorded.")
        timestamp = _now_utc() if decided_at is None else decided_at
        record = ApprovalDecisionRecord(
            request_id=request_id,
            decision=decision,
            approver=approver,
            reason=reason,
            decided_at=timestamp,
        )
        merged = tuple(sorted((*self.decisions, record), key=lambda item: item.request_id))
        return ApprovalWorkflow(requests=self.requests, decisions=merged)

    def request_for(self, request_id: str) -> ApprovalRequest | None:
        """Return the request with ``request_id`` (None when absent)."""
        for request in self.requests:
            if request.request_id == request_id:
                return request
        return None

    def decision_for(self, request_id: str) -> ApprovalDecisionRecord | None:
        """Return the decision for ``request_id`` (None when undecided)."""
        for decision in self.decisions:
            if decision.request_id == request_id:
                return decision
        return None

    def requires_approval(self, request_id: str) -> bool:
        """Whether the request is HIGH-risk and therefore needs approval."""
        request = self.request_for(request_id)
        return request is not None and request.risk_level is RiskLevel.HIGH

    def require_approved(self, request_id: str) -> None:
        """Raise unless a HIGH-risk request is approved (SP 4.39).

        NORMAL-risk requests pass without approval; a HIGH-risk request
        requires an APPROVED decision.

        Raises:
            PaperApprovalError: If the request is HIGH-risk and not approved.
        """
        if not self.requires_approval(request_id):
            return
        decision = self.decision_for(request_id)
        if decision is None or decision.decision is not ApprovalDecision.APPROVED:
            raise PaperApprovalError(
                f"Request {request_id!r} is high-risk and requires human approval."
            )

    def readable(self) -> str:
        """Render the workflow as a compact summary."""
        lines = [f"approval workflow: {len(self.requests)} request(s)"]
        for request in self.requests:
            lines.append(f"  {request.readable()}")
        for decision in self.decisions:
            lines.append(f"  {decision.readable()}")
        return "\n".join(lines)
