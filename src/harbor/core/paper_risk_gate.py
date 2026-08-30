"""Order risk gate for the paper loop (MVP 4 / SP 4.41).

Every order passes through the risk gate before submission: a frozen run
rejects new orders (SP 4.40), a blocking daily risk finding rejects the order
(SP 4.32), and a high-risk order requires human approval (SP 4.39). A rejected
order keeps the complete rejection reason (被拒订单保留完整拒绝原因) — never
silently dropped. The gate is deterministic: the same inputs always produce
the same decision.

Pure core logic: depends on the freeze, daily risk report and approval
workflow; never touches storage or CLI code.
"""

from dataclasses import dataclass

from harbor.core.paper_approval import ApprovalWorkflow, PaperApprovalError
from harbor.core.paper_domain import PaperOrder
from harbor.core.paper_freeze import FreezeState, reject_during_freeze
from harbor.core.paper_risk_checks import DailyRiskReport, RiskFindingSeverity


class PaperRiskGateError(ValueError):
    """Raised when an order cannot be gated (SP 4.41)."""


@dataclass(frozen=True)
class RiskGateOutcome:
    """The risk gate decision for one order (SP 4.41)."""

    order_id: str
    allowed: bool
    reasons: tuple[str, ...]
    requires_approval: bool = False

    def readable(self) -> str:
        """Render the gate outcome as a compact summary."""
        status = "allowed" if self.allowed else "REJECTED"
        lines = [f"risk gate {self.order_id}: {status}"]
        for reason in self.reasons:
            lines.append(f"  {reason}")
        if self.requires_approval:
            lines.append("  requires human approval")
        return "\n".join(lines)


def gate_order(
    *,
    order: PaperOrder,
    freeze: FreezeState | None = None,
    daily_report: DailyRiskReport | None = None,
    approval: ApprovalWorkflow | None = None,
    request_id: str | None = None,
) -> RiskGateOutcome:
    """Gate an order before submission (SP 4.41).

    Checks, in fixed order: the run freeze (SP 4.40), the daily risk report
    (SP 4.32) and the human-approval requirement (SP 4.39). The outcome keeps
    every rejection reason.

    Raises:
        PaperRiskGateError: If an approval request id is given without a
            workflow.
    """
    if request_id is not None and approval is None:
        raise PaperRiskGateError("An approval request id requires an approval workflow.")

    reasons: list[str] = []
    requires_approval = False

    if freeze is not None:
        reason = reject_during_freeze(freeze)
        if reason is not None:
            reasons.append(reason)

    if daily_report is not None and not daily_report.approved:
        for finding in daily_report.findings:
            if finding.severity is RiskFindingSeverity.ERROR and (
                finding.symbol is None or finding.symbol == order.symbol
            ):
                reasons.append(f"risk finding [{finding.kind.value}]: {finding.reason}")

    if approval is not None and request_id is not None and approval.requires_approval(request_id):
        requires_approval = True
        try:
            approval.require_approved(request_id)
        except PaperApprovalError as exc:
            reasons.append(str(exc))

    return RiskGateOutcome(
        order_id=order.order_id,
        allowed=not reasons,
        reasons=tuple(reasons),
        requires_approval=requires_approval,
    )
