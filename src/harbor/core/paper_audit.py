"""Audit log base for the paper loop (MVP 4 / SP 4.11).

Records every paper-loop event — signal, approval, order, fill, circuit
breaker and manual operation — as an immutable, UTC-stamped audit entry so the
loop is fully auditable and replayable (SP 4.11 acceptance). The
:class:`PaperAuditLog` is immutable: appending returns a new log with the next
monotonic sequence number, and the fingerprint is a deterministic SHA-256 over
the canonical event serialization so equal event streams replay identically
(SP 4.59).

Pure core logic: depends on the domain types and never touches storage or CLI
code.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class AuditEventType(StrEnum):
    """The kinds of paper-loop events recorded (SP 4.11)."""

    SIGNAL = "SIGNAL"
    APPROVAL = "APPROVAL"
    ORDER = "ORDER"
    FILL = "FILL"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    MANUAL = "MANUAL"


class AuditActor(StrEnum):
    """Who caused the audit event (SP 4.11)."""

    SYSTEM = "SYSTEM"
    APPROVER = "APPROVER"
    MANUAL = "MANUAL"


class PaperAuditError(ValueError):
    """Raised when an audit event or log is invalid (SP 4.11)."""


def _require_utc_aware(timestamp: datetime, what: str) -> None:
    """Require an explicit UTC offset so audit timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError(f"{what} must be UTC-aware (offset 0).")


@dataclass(frozen=True)
class PaperAuditEvent:
    """One immutable audit event (SP 4.11).

    ``sequence`` is the monotonic event number within its log; ``detail`` is a
    human-readable description. The actor distinguishes system actions from
    human/approver actions (SP 4.39).
    """

    event_id: str
    paper_run_id: str
    event_type: AuditEventType
    actor: AuditActor
    detail: str
    recorded_at: datetime
    sequence: int

    def __post_init__(self) -> None:
        if not self.event_id or not self.paper_run_id:
            raise PaperAuditError("Event id and paper run id must be non-empty.")
        if not self.detail:
            raise PaperAuditError("Event detail must be non-empty.")
        if self.sequence < 0:
            raise PaperAuditError("Event sequence must be non-negative.")
        _require_utc_aware(self.recorded_at, "Audit event time")

    def readable(self) -> str:
        """Render the event as one audit line."""
        return (
            f"[{self.sequence}] {self.event_type.value} {self.event_id} "
            f"run {self.paper_run_id} actor {self.actor.value} "
            f"at {self.recorded_at.isoformat()} {self.detail}"
        )


@dataclass(frozen=True)
class PaperAuditLog:
    """An immutable, replayable audit log (SP 4.11).

    Events are stored in sequence order (strictly increasing ``sequence``);
    appending assigns the next sequence number. A hand-edited log with
    duplicate, missing or out-of-order sequences is rejected rather than
    trusted.
    """

    events: tuple[PaperAuditEvent, ...] = ()

    def __post_init__(self) -> None:
        for index, event in enumerate(self.events):
            if index > 0 and event.sequence <= self.events[index - 1].sequence:
                raise PaperAuditError("Audit event sequences must be strictly increasing.")

    def append(
        self,
        *,
        paper_run_id: str | None = None,
        event_id: str,
        event_type: AuditEventType,
        actor: AuditActor,
        detail: str,
        recorded_at: datetime,
    ) -> "PaperAuditLog":
        """Return a new log with the next event appended (SP 4.11).

        The sequence is the successor of the last event's sequence (0 for an
        empty log). ``paper_run_id`` is derived from the first event when
        omitted; it must be supplied for the first event of a log.
        """
        sequence = self.events[-1].sequence + 1 if self.events else 0
        run_id = (
            paper_run_id
            if paper_run_id is not None
            else (self.events[0].paper_run_id if self.events else "")
        )
        event = PaperAuditEvent(
            event_id=event_id,
            paper_run_id=run_id,
            event_type=event_type,
            actor=actor,
            detail=detail,
            recorded_at=recorded_at,
            sequence=sequence,
        )
        return PaperAuditLog(events=(*self.events, event))

    def events_for(self, event_type: AuditEventType) -> tuple[PaperAuditEvent, ...]:
        """Return the events of one type, in sequence order."""
        return tuple(event for event in self.events if event.event_type is event_type)

    def readable(self) -> str:
        """Render the full audit trail."""
        lines = [f"paper audit log: {len(self.events)} events"]
        for event in self.events:
            lines.append(f"  {event.readable()}")
        return "\n".join(lines)


def audit_json(log: PaperAuditLog) -> str:
    """Return the canonical, key-sorted JSON of the audit log (SP 4.11).

    Timestamps are serialized as ISO strings and events keep their sequence
    order, so equal logs serialize identically (replayability).
    """
    payload = [
        {
            "sequence": event.sequence,
            "event_id": event.event_id,
            "paper_run_id": event.paper_run_id,
            "event_type": event.event_type.value,
            "actor": event.actor.value,
            "detail": event.detail,
            "recorded_at": event.recorded_at.isoformat(),
        }
        for event in log.events
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def audit_fingerprint(log: PaperAuditLog) -> str:
    """Return a stable SHA-256 digest over the canonical event stream (SP 4.11)."""
    return hashlib.sha256(audit_json(log).encode("utf-8")).hexdigest()


def log_audit_event(
    log: PaperAuditLog,
    *,
    event_id: str,
    event_type: AuditEventType,
    actor: AuditActor,
    detail: str,
    recorded_at: datetime,
) -> PaperAuditLog:
    """Convenience alias for :meth:`PaperAuditLog.append` (SP 4.11)."""
    return log.append(
        event_id=event_id,
        event_type=event_type,
        actor=actor,
        detail=detail,
        recorded_at=recorded_at,
    )


def new_paper_audit_log(paper_run_id: str, *, recorded_at: datetime) -> PaperAuditLog:
    """Create an audit log seeded with a run-opened event (SP 4.11)."""
    log = PaperAuditLog()
    return log.append(
        paper_run_id=paper_run_id,
        event_id=f"open-{paper_run_id}",
        event_type=AuditEventType.MANUAL,
        actor=AuditActor.MANUAL,
        detail=f"paper run {paper_run_id} opened",
        recorded_at=recorded_at,
    )
