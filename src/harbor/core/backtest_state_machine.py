"""Backtest run state machine (MVP 2 / SP 2.46).

Defines the lifecycle of a backtest run: INITIALIZING -> RUNNING ->
COMPLETED, with RUNNING also able to reach FAILED or CANCELLED, and
INITIALIZING able to fail (e.g. a preflight error). COMPLETED, FAILED and
CANCELLED are terminal: a failed run is never resumed silently — resuming must
create a new run linked to the original (SP 2.70).

A failed run retains the diagnostics generated so far (SP 2.46 acceptance):
warnings accumulated during the run plus the failure's error summary and the
stage (SP 2.47 phases) at which it failed. The state machine is pure core
logic; the storage layer (SP 2.6) persists the status.
"""

from dataclasses import dataclass, replace

from harbor.core.backtest_domain import BacktestStatus

_ALLOWED: dict[BacktestStatus, frozenset[BacktestStatus]] = {
    BacktestStatus.INITIALIZING: frozenset(
        {BacktestStatus.RUNNING, BacktestStatus.FAILED, BacktestStatus.CANCELLED}
    ),
    BacktestStatus.RUNNING: frozenset(
        {BacktestStatus.COMPLETED, BacktestStatus.FAILED, BacktestStatus.CANCELLED}
    ),
    BacktestStatus.COMPLETED: frozenset(),
    BacktestStatus.FAILED: frozenset(),
    BacktestStatus.CANCELLED: frozenset(),
}


class StateTransitionError(ValueError):
    """Raised when a run attempts an invalid state transition (SP 2.46)."""


def allowed_transitions(status: BacktestStatus) -> frozenset[BacktestStatus]:
    """Return the states reachable directly from ``status`` (SP 2.46)."""
    return _ALLOWED[status]


def can_transition(current: BacktestStatus, new: BacktestStatus) -> bool:
    """Whether ``current`` may transition to ``new`` (SP 2.46)."""
    return new in _ALLOWED[current]


@dataclass(frozen=True)
class RunDiagnostics:
    """Diagnostics retained when a run fails or is cancelled (SP 2.46).

    ``warnings`` accumulate while the run is in progress (diagnostics generated
    so far); ``error_summary`` and ``stage`` are set when the run fails.
    """

    error_summary: str | None = None
    warnings: tuple[str, ...] = ()
    stage: str | None = None

    def readable(self) -> str:
        """Render the diagnostics as a human-readable summary."""
        lines: list[str] = []
        if self.stage is not None:
            lines.append(f"  stage: {self.stage}")
        if self.error_summary is not None:
            lines.append(f"  error: {self.error_summary}")
        for warning in self.warnings:
            lines.append(f"  warning: {warning}")
        return "\n".join(lines)


@dataclass(frozen=True)
class RunState:
    """Immutable state of one backtest run (SP 2.46)."""

    run_id: str
    status: BacktestStatus
    diagnostics: RunDiagnostics = RunDiagnostics()

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be non-empty.")

    def transition(self, new_status: BacktestStatus) -> "RunState":
        """Transition to ``new_status``, rejecting invalid moves (SP 2.46).

        Raises:
            StateTransitionError: If the transition is not allowed.
        """
        if not can_transition(self.status, new_status):
            raise StateTransitionError(
                f"Invalid backtest state transition for run {self.run_id!r}: "
                f"{self.status.value} -> {new_status.value}."
            )
        return replace(self, status=new_status)

    def start(self) -> "RunState":
        """Begin the run (INITIALIZING -> RUNNING)."""
        return self.transition(BacktestStatus.RUNNING)

    def complete(self) -> "RunState":
        """Finish the run successfully (RUNNING -> COMPLETED)."""
        return self.transition(BacktestStatus.COMPLETED)

    def cancel(self) -> "RunState":
        """Cancel the run (INITIALIZING/RUNNING -> CANCELLED)."""
        return self.transition(BacktestStatus.CANCELLED)

    def with_warning(self, message: str) -> "RunState":
        """Accumulate a diagnostic warning while the run is in progress."""
        return replace(
            self,
            diagnostics=replace(
                self.diagnostics,
                warnings=self.diagnostics.warnings + (message,),
            ),
        )

    def fail(self, error_summary: str, *, stage: str | None = None) -> "RunState":
        """Mark the run as failed, retaining diagnostics (SP 2.46).

        The accumulated warnings are kept, and the failure's ``error_summary``
        and ``stage`` are recorded so a failed run is diagnosable.

        Raises:
            ValueError: If ``error_summary`` is empty.
            StateTransitionError: If the current state cannot fail (already
                terminal).
        """
        if not error_summary:
            raise ValueError("error_summary must be non-empty.")
        failed = self.transition(BacktestStatus.FAILED)
        return replace(
            failed,
            diagnostics=replace(
                failed.diagnostics,
                error_summary=error_summary,
                stage=stage,
            ),
        )

    def readable(self) -> str:
        """Render the run state as a human-readable summary."""
        lines = [
            f"run {self.run_id}: {self.status.value}",
        ]
        diagnostics = self.diagnostics.readable()
        if diagnostics:
            lines.append(diagnostics)
        return "\n".join(lines)


def initial_state(run_id: str) -> RunState:
    """Create a new run in the INITIALIZING state (SP 2.46)."""
    return RunState(run_id=run_id, status=BacktestStatus.INITIALIZING)
