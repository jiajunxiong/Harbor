"""Resume / cancel policy for backtest runs (SP 2.70).

A failed or cancelled run must never be silently continued under its own id:
resuming always creates a **new** run that is linked to the original via
``resume_of``. A completed run is reused (SP 2.48) and a run that is still
initializing or running must be cancelled before it can be resumed. This
module is pure: it only decides the policy, the service layer applies it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from harbor.core.backtest_domain import BacktestStatus
from harbor.core.backtest_state_machine import can_transition


class ResumePolicyError(ValueError):
    """Raised for an invalid resume/cancel policy query."""


class ResumeAction(StrEnum):
    """The action implied by a resume decision."""

    NEW_RUN = "new_run"
    REUSE = "reuse"
    REJECT = "reject"


@dataclass(frozen=True)
class ResumeDecision:
    """The policy outcome for a proposed resume of an existing run."""

    run_id: str
    status: BacktestStatus
    action: ResumeAction
    new_run_id: str | None
    reason: str

    def readable(self) -> str:
        """Human-readable one-line rendering for CLI/log output."""
        new_run = self.new_run_id if self.new_run_id is not None else "—"
        return (
            f"Resume decision for {self.run_id} ({self.status.value}): "
            f"{self.action.value} -> {new_run}; {self.reason}"
        )


def decide_resume(
    *,
    run_id: str,
    status: BacktestStatus,
    new_run_id: str | None = None,
) -> ResumeDecision:
    """Decide how a resume of ``run_id`` (in ``status``) must proceed.

    * INITIALIZING / RUNNING -> reject: the run is still in progress and must
      be cancelled before it can be resumed.
    * COMPLETED -> reuse: an identical re-run is served from the store
      (SP 2.48), there is nothing to resume.
    * FAILED / CANCELLED -> new run: never continue in place; create a fresh
      run linked back to the original via ``resume_of``.
    """
    if status is BacktestStatus.INITIALIZING:
        return ResumeDecision(
            run_id=run_id,
            status=status,
            action=ResumeAction.REJECT,
            new_run_id=None,
            reason="run is still initializing; cancel it before resuming.",
        )
    if status is BacktestStatus.RUNNING:
        return ResumeDecision(
            run_id=run_id,
            status=status,
            action=ResumeAction.REJECT,
            new_run_id=None,
            reason="run is still in progress; cancel it before resuming.",
        )
    if status is BacktestStatus.COMPLETED:
        return ResumeDecision(
            run_id=run_id,
            status=status,
            action=ResumeAction.REUSE,
            new_run_id=None,
            reason="run already completed; reuse it instead of resuming.",
        )
    if status is BacktestStatus.FAILED:
        return ResumeDecision(
            run_id=run_id,
            status=status,
            action=ResumeAction.NEW_RUN,
            new_run_id=new_run_id,
            reason="failed runs never resume silently; a new run links to the original.",
        )
    if status is BacktestStatus.CANCELLED:
        return ResumeDecision(
            run_id=run_id,
            status=status,
            action=ResumeAction.NEW_RUN,
            new_run_id=new_run_id,
            reason="cancelled runs resume as a new run linked to the original.",
        )
    raise ResumePolicyError(f"Unknown backtest status {status!r} for resume policy.")


def can_cancel(status: BacktestStatus) -> bool:
    """Whether a run in ``status`` may be cancelled (SP 2.46 state machine)."""
    return can_transition(status, BacktestStatus.CANCELLED)
