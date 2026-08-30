"""Research boundary review (MVP 4 / SP 4.83).

The pre-release research boundary review confirms the paper loop never crosses
into execution: it produces no broker orders and holds no broker credentials,
its reports carry no return or drawdown promise, and it cannot be upgraded to
MVP 5 until the difference verification admission has passed (发布前研究边界复核,
SP 4.83). Failing any of the three blocks the review.

The no-return-promise statement is reused from SP 3.64 so the boundary review
and the OOS conclusion share the same wording (研究性质与停止条件).

Pure core logic: depends on the OOS conclusion's no-return-promise statement;
never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date


class ResearchBoundaryError(ValueError):
    """Raised when a research boundary review is invalid (SP 4.83)."""


@dataclass(frozen=True)
class ResearchBoundaryReview:
    """The pre-release research boundary review (SP 4.83)."""

    paper_run_id: str
    reviewed_at: date
    reviewer: str
    no_broker_orders: bool
    no_return_promise: bool
    admission_passed: bool
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.paper_run_id:
            raise ResearchBoundaryError("paper_run_id must be non-empty.")
        if not self.reviewer:
            raise ResearchBoundaryError("reviewer must be non-empty.")

    @property
    def passed(self) -> bool:
        """Whether the boundary review passes (SP 4.83)."""
        return self.no_broker_orders and self.no_return_promise and self.admission_passed

    def readable(self) -> str:
        """Render the review as a compact summary."""
        status = "PASSED" if self.passed else "BLOCKED"
        lines = [
            f"research boundary review for {self.paper_run_id} on "
            f"{self.reviewed_at.isoformat()} by {self.reviewer}: {status}"
        ]
        lines.append(f"  no broker orders/credentials: {'yes' if self.no_broker_orders else 'NO'}")
        lines.append(f"  no return/drawdown promise: {'yes' if self.no_return_promise else 'NO'}")
        lines.append(
            f"  difference verification admission passed: "
            f"{'yes' if self.admission_passed else 'NO'}"
        )
        if self.notes:
            lines.append(f"  notes: {self.notes}")
        return "\n".join(lines)


def review_research_boundary(
    *,
    paper_run_id: str,
    reviewed_at: date,
    reviewer: str,
    no_broker_orders: bool,
    no_return_promise: bool,
    admission_passed: bool,
    notes: str = "",
) -> ResearchBoundaryReview:
    """Run the pre-release research boundary review (SP 4.83).

    Confirms the paper path produces no broker orders, reports carry no return
    promise, and the difference verification admission has passed; a single
    failure blocks the review.
    """
    return ResearchBoundaryReview(
        paper_run_id=paper_run_id,
        reviewed_at=reviewed_at,
        reviewer=reviewer,
        no_broker_orders=no_broker_orders,
        no_return_promise=no_return_promise,
        admission_passed=admission_passed,
        notes=notes,
    )
