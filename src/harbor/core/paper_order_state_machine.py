"""Paper order state machine (MVP 4 / SP 4.20).

Defines the lifecycle of a paper order over the SP 4.1 ``PaperOrderStatus``
vocabulary: ``CREATED`` -> ``SUBMITTED`` -> (``PARTIALLY_FILLED``) ->
``FILLED``. An order may be ``CANCELLED`` (including the remainder of a
partially-filled order) or ``REJECTED`` while it is still actionable;
``FILLED``, ``CANCELLED`` and ``REJECTED`` are terminal. Every transition is
recorded in an immutable, UTC-stamped audit trail whose chain is
self-validating, so the order lifecycle is fully auditable and replayable.

Pure core logic: depends on the paper domain and never touches storage or CLI
code.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from harbor.core.paper_domain import PaperOrderStatus

_ALLOWED: dict[PaperOrderStatus, frozenset[PaperOrderStatus]] = {
    PaperOrderStatus.CREATED: frozenset(
        {PaperOrderStatus.SUBMITTED, PaperOrderStatus.CANCELLED, PaperOrderStatus.REJECTED}
    ),
    PaperOrderStatus.SUBMITTED: frozenset(
        {
            PaperOrderStatus.PARTIALLY_FILLED,
            PaperOrderStatus.FILLED,
            PaperOrderStatus.CANCELLED,
            PaperOrderStatus.REJECTED,
        }
    ),
    PaperOrderStatus.PARTIALLY_FILLED: frozenset(
        {PaperOrderStatus.FILLED, PaperOrderStatus.CANCELLED}
    ),
    PaperOrderStatus.FILLED: frozenset(),
    PaperOrderStatus.CANCELLED: frozenset(),
    PaperOrderStatus.REJECTED: frozenset(),
}


class PaperOrderStateError(ValueError):
    """Raised when a paper order attempts an invalid transition (SP 4.20)."""


def _now_utc() -> datetime:
    """Return the current UTC time (default transition timestamp)."""
    return datetime.now(timezone.utc)


def _require_utc_aware(timestamp: datetime) -> None:
    """Require an explicit UTC offset so audit timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("Paper order transition timestamps must be UTC-aware (offset 0).")


def allowed_transitions(status: PaperOrderStatus) -> frozenset[PaperOrderStatus]:
    """Return the states reachable directly from ``status`` (SP 4.20)."""
    return _ALLOWED[status]


def can_transition(current: PaperOrderStatus, new: PaperOrderStatus) -> bool:
    """Whether ``current`` may transition to ``new`` (SP 4.20)."""
    return new in _ALLOWED[current]


@dataclass(frozen=True)
class PaperOrderTransition:
    """One recorded order state transition in the audit trail (SP 4.20)."""

    from_status: PaperOrderStatus
    to_status: PaperOrderStatus
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


def _validate_chain(
    status: PaperOrderStatus, transitions: tuple[PaperOrderTransition, ...]
) -> None:
    """Require the audit trail to be contiguous and end at ``status`` (SP 4.20)."""
    previous_target: PaperOrderStatus | None = None
    for index, entry in enumerate(transitions):
        if previous_target is not None and entry.from_status is not previous_target:
            raise PaperOrderStateError(
                f"transition {index} does not follow the previous target ({previous_target.value})."
            )
        previous_target = entry.to_status
    if transitions:
        assert previous_target is not None
        if previous_target is not status:
            raise PaperOrderStateError(
                f"final transition targets {previous_target.value}, but the "
                f"current status is {status.value}."
            )


@dataclass(frozen=True)
class PaperOrderState:
    """An auditable, replayable paper order state (SP 4.20)."""

    order_id: str
    status: PaperOrderStatus = PaperOrderStatus.CREATED
    transitions: tuple[PaperOrderTransition, ...] = ()

    def __post_init__(self) -> None:
        if not self.order_id:
            raise PaperOrderStateError("order_id must be non-empty.")
        _validate_chain(self.status, self.transitions)

    def transition(
        self,
        new_status: PaperOrderStatus,
        *,
        reason: str | None = None,
        recorded_at: datetime | None = None,
    ) -> "PaperOrderState":
        """Return a new order state advanced to ``new_status`` (SP 4.20).

        Raises:
            PaperOrderStateError: If the transition is not legal or the
                recorded timestamp is not UTC-aware.
        """
        if not can_transition(self.status, new_status):
            raise PaperOrderStateError(
                f"cannot transition order {self.order_id} from "
                f"{self.status.value} to {new_status.value}."
            )
        timestamp = _now_utc() if recorded_at is None else recorded_at
        _require_utc_aware(timestamp)
        entry = PaperOrderTransition(
            from_status=self.status,
            to_status=new_status,
            recorded_at=timestamp,
            reason=reason,
        )
        return replace(
            self,
            status=new_status,
            transitions=(*self.transitions, entry),
        )

    def submit(
        self,
        *,
        reason: str = "order submitted",
        recorded_at: datetime | None = None,
    ) -> "PaperOrderState":
        """Submit the created order for execution."""
        return self.transition(PaperOrderStatus.SUBMITTED, reason=reason, recorded_at=recorded_at)

    def fill_partial(
        self,
        *,
        reason: str = "order partially filled",
        recorded_at: datetime | None = None,
    ) -> "PaperOrderState":
        """Record a partial fill of the submitted order."""
        return self.transition(
            PaperOrderStatus.PARTIALLY_FILLED, reason=reason, recorded_at=recorded_at
        )

    def complete(
        self,
        *,
        reason: str = "order fully filled",
        recorded_at: datetime | None = None,
    ) -> "PaperOrderState":
        """Record the order as fully filled."""
        return self.transition(PaperOrderStatus.FILLED, reason=reason, recorded_at=recorded_at)

    def cancel(
        self,
        *,
        reason: str = "order cancelled",
        recorded_at: datetime | None = None,
    ) -> "PaperOrderState":
        """Cancel the order (including the remainder of a partial fill)."""
        return self.transition(PaperOrderStatus.CANCELLED, reason=reason, recorded_at=recorded_at)

    def reject(
        self,
        *,
        reason: str,
        recorded_at: datetime | None = None,
    ) -> "PaperOrderState":
        """Reject the order with a mandatory reason (SP 4.41)."""
        return self.transition(PaperOrderStatus.REJECTED, reason=reason, recorded_at=recorded_at)

    def readable(self) -> str:
        """Render the order state with its full audit trail."""
        lines = [f"paper order {self.order_id} status {self.status.value}"]
        for entry in self.transitions:
            lines.append(f"  {entry.readable()}")
        return "\n".join(lines)


def paper_order_initial_state(order_id: str) -> PaperOrderState:
    """Create a fresh paper order in ``CREATED`` (SP 4.20)."""
    return PaperOrderState(order_id=order_id)
