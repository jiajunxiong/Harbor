"""Freeze state for the paper loop (MVP 4 / SP 4.40).

A frozen paper run rejects new orders (禁止开仓); the freeze and its recovery
are both recorded with their UTC timestamps and reasons, so a freeze is never
silent. :class:`FreezeState` is the operative gate the orchestration checks;
the persistent record is the SP 4.7 ``circuit_breakers`` table.

Pure core logic: depends on stdlib datetime and never touches storage or CLI
code.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class PaperFreezeError(ValueError):
    """Raised when a freeze state is invalid (SP 4.40)."""


def _now_utc() -> datetime:
    """Return the current UTC time (default freeze timestamp)."""
    return datetime.now(timezone.utc)


def _require_utc_aware(timestamp: datetime, what: str) -> None:
    """Require an explicit UTC offset so timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError(f"{what} must be UTC-aware (offset 0).")


@dataclass(frozen=True)
class FreezeState:
    """Whether and how a paper run is frozen (SP 4.40).

    ``scope`` names what is frozen (e.g. ``new_orders``). A frozen state
    carries a ``frozen_at`` and no recovery; a recovered state carries a
    ``recovered_at``.
    """

    paper_run_id: str
    frozen: bool
    scope: str
    reason: str
    frozen_at: datetime
    recovered_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.paper_run_id or not self.scope or not self.reason:
            raise PaperFreezeError("paper_run_id, scope and reason must be non-empty.")
        _require_utc_aware(self.frozen_at, "Freeze time")
        if self.recovered_at is not None:
            _require_utc_aware(self.recovered_at, "Recovery time")
            if self.recovered_at < self.frozen_at:
                raise PaperFreezeError("recovered_at must be on or after frozen_at.")
        if self.frozen and self.recovered_at is not None:
            raise PaperFreezeError("A frozen run cannot already be recovered.")
        if not self.frozen and self.recovered_at is None:
            raise PaperFreezeError("A recovered run must record a recovery time.")

    @property
    def is_frozen(self) -> bool:
        """Whether the run is currently frozen."""
        return self.frozen

    def blocks_new_orders(self) -> bool:
        """Whether new orders are blocked by this freeze (SP 4.40)."""
        return self.frozen and "new_orders" in self.scope

    def readable(self) -> str:
        """Render the freeze state as a compact summary."""
        state = "FROZEN" if self.frozen else "RECOVERED"
        recovery = (
            "" if self.recovered_at is None else f" recovered {self.recovered_at.isoformat()}"
        )
        return (
            f"paper run {self.paper_run_id} {state} scope {self.scope} "
            f"reason {self.reason} frozen {self.frozen_at.isoformat()}{recovery}"
        )


def freeze_run(
    *,
    paper_run_id: str,
    scope: str = "new_orders",
    reason: str,
    frozen_at: datetime | None = None,
) -> FreezeState:
    """Freeze a paper run (SP 4.40).

    Raises:
        PaperFreezeError: If the identity fields are empty or the timestamp is
            not UTC-aware.
    """
    timestamp = _now_utc() if frozen_at is None else frozen_at
    return FreezeState(
        paper_run_id=paper_run_id,
        frozen=True,
        scope=scope,
        reason=reason,
        frozen_at=timestamp,
    )


def recover_freeze(
    *,
    state: FreezeState,
    recovered_at: datetime | None = None,
) -> FreezeState:
    """Recover a frozen paper run (SP 4.40).

    Raises:
        PaperFreezeError: If ``state`` is not frozen (a recovered run cannot be
            recovered again) or the timestamp is not UTC-aware.
    """
    if not state.frozen:
        raise PaperFreezeError("Cannot recover a run that is not frozen.")
    timestamp = _now_utc() if recovered_at is None else recovered_at
    _require_utc_aware(timestamp, "Recovery time")
    if timestamp < state.frozen_at:
        raise PaperFreezeError("recovered_at must be on or after frozen_at.")
    return FreezeState(
        paper_run_id=state.paper_run_id,
        frozen=False,
        scope=state.scope,
        reason=state.reason,
        frozen_at=state.frozen_at,
        recovered_at=timestamp,
    )


def reject_during_freeze(state: FreezeState) -> str | None:
    """Return the rejection reason for a new order, or None if allowed (SP 4.40).

    When the run is frozen for ``new_orders``, every new order is rejected with
    the recorded reason (never silently dropped).
    """
    if state.blocks_new_orders():
        return f"order rejected: run {state.paper_run_id} is frozen (reason: {state.reason})."
    return None
