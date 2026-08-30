"""Auditable paper-trading state machine (MVP 4 / SP 4.10).

Defines the lifecycle of a paper run over the SP 4.1 ``PaperStatus``
vocabulary: ``DRAFT`` -> ``APPROVED`` -> ``ACTIVE``. A run is drafted, must be
approved (SP 4.7 / 4.39 human-approval gate) before it can become ``ACTIVE``.
An active run may be ``PAUSED`` or trip a circuit breaker into
``CIRCUIT_BROKEN`` (SP 4.36); a circuit-broken run returns to ``ACTIVE`` only
after recovery (SP 4.42) or stops. ``STOPPED`` is terminal — a stopped run is
never silently resumed.

Every transition is recorded in an immutable audit trail: a
``PaperTransition`` entry with the source/target statuses, a UTC timestamp and
a reason. The chain is self-validating (each transition must continue from the
previous target and end at the current status), so the state machine is fully
auditable and replayable. Pure core logic; the storage layer (SP 4.5) persists
the status.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from harbor.core.paper_domain import PaperStatus

_ALLOWED: dict[PaperStatus, frozenset[PaperStatus]] = {
    PaperStatus.DRAFT: frozenset({PaperStatus.APPROVED, PaperStatus.STOPPED}),
    PaperStatus.APPROVED: frozenset({PaperStatus.ACTIVE, PaperStatus.STOPPED}),
    PaperStatus.ACTIVE: frozenset(
        {PaperStatus.PAUSED, PaperStatus.CIRCUIT_BROKEN, PaperStatus.STOPPED}
    ),
    PaperStatus.PAUSED: frozenset({PaperStatus.ACTIVE, PaperStatus.STOPPED}),
    PaperStatus.CIRCUIT_BROKEN: frozenset({PaperStatus.ACTIVE, PaperStatus.STOPPED}),
    PaperStatus.STOPPED: frozenset(),
}

_REASON_STATES = (PaperStatus.CIRCUIT_BROKEN, PaperStatus.STOPPED)


class PaperStateError(ValueError):
    """Raised when a paper run attempts an invalid transition (SP 4.10)."""


def _now_utc() -> datetime:
    """Return the current UTC time (default transition timestamp)."""
    return datetime.now(timezone.utc)


def _require_utc_aware(timestamp: datetime) -> None:
    """Require an explicit UTC offset so audit timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("Paper transition timestamps must be UTC-aware (offset 0).")


def allowed_transitions(status: PaperStatus) -> frozenset[PaperStatus]:
    """Return the states reachable directly from ``status`` (SP 4.10)."""
    return _ALLOWED[status]


def can_transition(current: PaperStatus, new: PaperStatus) -> bool:
    """Whether ``current`` may transition to ``new`` (SP 4.10)."""
    return new in _ALLOWED[current]


@dataclass(frozen=True)
class PaperTransition:
    """One recorded state transition in the audit trail (SP 4.10)."""

    from_status: PaperStatus
    to_status: PaperStatus
    recorded_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_utc_aware(self.recorded_at)

    def readable(self) -> str:
        """Render the transition as one audit line."""
        reason = f" ({self.reason})" if self.reason is not None else ""
        return (
            f"{self.from_status.value} -> {self.to_status.value} "
            f"at {self.recorded_at.isoformat()}{reason}"
        )


@dataclass(frozen=True)
class PaperDiagnostics:
    """Diagnostics retained by a paper run (SP 4.10).

    ``warnings`` accumulate while the run is active; ``reason`` records why
    the run was circuit-broken or stopped (never a silent stop).
    """

    warnings: tuple[str, ...] = ()
    reason: str | None = None

    def readable(self) -> str:
        """Render the diagnostics as a compact summary."""
        lines = [f"reason: {self.reason}" if self.reason is not None else "reason: none"]
        for warning in self.warnings:
            lines.append(f"warning: {warning}")
        return "; ".join(lines)


def _validate_transition_chain(
    status: PaperStatus, transitions: tuple[PaperTransition, ...]
) -> None:
    """Require the audit trail to be contiguous and end at ``status`` (SP 4.10).

    A hand-edited or truncated trail is rejected rather than trusted: each
    entry must continue from the previous entry's target and the last entry
    must target the current status.
    """
    previous_target: PaperStatus | None = None
    for index, entry in enumerate(transitions):
        if previous_target is not None and entry.from_status is not previous_target:
            raise PaperStateError(
                f"transition {index} does not follow the previous target ({previous_target.value})."
            )
        previous_target = entry.to_status
    if transitions:
        assert previous_target is not None
        if previous_target is not status:
            raise PaperStateError(
                f"final transition targets {previous_target.value}, but the "
                f"current status is {status.value}."
            )


@dataclass(frozen=True)
class PaperRunState:
    """An auditable, replayable paper run state (SP 4.10)."""

    run_id: str
    status: PaperStatus = PaperStatus.DRAFT
    diagnostics: PaperDiagnostics = PaperDiagnostics()
    transitions: tuple[PaperTransition, ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id:
            raise PaperStateError("run_id must be non-empty.")
        _validate_transition_chain(self.status, self.transitions)

    def transition(
        self,
        new_status: PaperStatus,
        *,
        reason: str | None = None,
        recorded_at: datetime | None = None,
    ) -> "PaperRunState":
        """Return a new state advanced to ``new_status`` with an audit entry.

        Raises:
            PaperStateError: If the transition is not legal (SP 4.10) or the
                recorded timestamp is not UTC-aware.
        """
        if not can_transition(self.status, new_status):
            raise PaperStateError(
                f"cannot transition from {self.status.value} to {new_status.value}."
            )
        timestamp = _now_utc() if recorded_at is None else recorded_at
        _require_utc_aware(timestamp)
        entry = PaperTransition(
            from_status=self.status,
            to_status=new_status,
            recorded_at=timestamp,
            reason=reason,
        )
        diagnostics = self.diagnostics
        if new_status in _REASON_STATES and reason is not None:
            diagnostics = replace(diagnostics, reason=reason)
        return replace(
            self,
            status=new_status,
            diagnostics=diagnostics,
            transitions=(*self.transitions, entry),
        )

    def approve(
        self,
        *,
        reason: str = "run approved for the paper loop",
        recorded_at: datetime | None = None,
    ) -> "PaperRunState":
        """Approve the run (SP 4.10 / 4.39)."""
        return self.transition(PaperStatus.APPROVED, reason=reason, recorded_at=recorded_at)

    def activate(
        self,
        *,
        reason: str = "run activated",
        recorded_at: datetime | None = None,
    ) -> "PaperRunState":
        """Activate the approved run."""
        return self.transition(PaperStatus.ACTIVE, reason=reason, recorded_at=recorded_at)

    def pause(
        self,
        *,
        reason: str = "run paused",
        recorded_at: datetime | None = None,
    ) -> "PaperRunState":
        """Pause the active run."""
        return self.transition(PaperStatus.PAUSED, reason=reason, recorded_at=recorded_at)

    def break_circuit(
        self,
        *,
        reason: str,
        recorded_at: datetime | None = None,
    ) -> "PaperRunState":
        """Trip a circuit breaker into ``CIRCUIT_BROKEN`` (SP 4.36).

        ``reason`` is mandatory so a breaker never trips silently.
        """
        return self.transition(PaperStatus.CIRCUIT_BROKEN, reason=reason, recorded_at=recorded_at)

    def recover(
        self,
        *,
        reason: str = "circuit breaker recovered after review",
        recorded_at: datetime | None = None,
    ) -> "PaperRunState":
        """Recover a circuit-broken run back to ``ACTIVE`` (SP 4.42)."""
        return self.transition(PaperStatus.ACTIVE, reason=reason, recorded_at=recorded_at)

    def stop(
        self,
        *,
        reason: str = "run stopped",
        recorded_at: datetime | None = None,
    ) -> "PaperRunState":
        """Stop the run (terminal state)."""
        return self.transition(PaperStatus.STOPPED, reason=reason, recorded_at=recorded_at)

    def with_warning(self, message: str) -> "PaperRunState":
        """Return a new state with ``message`` appended to the warnings."""
        if not message:
            raise PaperStateError("warning message must be non-empty.")
        return replace(
            self,
            diagnostics=replace(self.diagnostics, warnings=(*self.diagnostics.warnings, message)),
        )

    def readable(self) -> str:
        """Render the run state with its full audit trail."""
        lines = [
            f"paper run {self.run_id} status {self.status.value}",
            f"  {self.diagnostics.readable()}",
        ]
        for entry in self.transitions:
            lines.append(f"  {entry.readable()}")
        return "\n".join(lines)


def paper_initial_state(run_id: str) -> PaperRunState:
    """Create a fresh paper run in ``DRAFT`` (SP 4.10)."""
    return PaperRunState(run_id=run_id)
