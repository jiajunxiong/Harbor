"""Paper execution latency model (MVP 4 / SP 4.23).

Records the signal -> order -> fill timestamps and derives the per-leg
latencies (in seconds) so the difference between the paper execution and the
research assumptions can be analyzed (SP 4.69 / 4.71). Timestamps must be
UTC-aware and ordered (signal on or before order on or before fill); an
out-of-order or naive series is rejected rather than silently normalized.

Pure core logic: depends on stdlib datetime and never touches storage or CLI
code.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


class PaperExecutionLatencyError(ValueError):
    """Raised when an execution latency series is invalid (SP 4.23)."""


def _require_utc_aware(timestamp: datetime, what: str) -> None:
    """Require an explicit UTC offset so timestamps are never naive/local."""
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError(f"{what} must be UTC-aware (offset 0).")


@dataclass(frozen=True)
class ExecutionLatency:
    """The recorded execution latency of one paper order (SP 4.23)."""

    signal_created_at: datetime
    order_created_at: datetime
    fill_created_at: datetime
    signal_to_order_seconds: float
    order_to_fill_seconds: float
    total_seconds: float

    def __post_init__(self) -> None:
        _require_utc_aware(self.signal_created_at, "Signal time")
        _require_utc_aware(self.order_created_at, "Order time")
        _require_utc_aware(self.fill_created_at, "Fill time")
        if not (self.signal_created_at <= self.order_created_at <= self.fill_created_at):
            raise PaperExecutionLatencyError(
                "Execution timestamps must be ordered: signal <= order <= fill."
            )

    def readable(self) -> str:
        """Render the latency as a compact summary."""
        return (
            f"signal {self.signal_created_at.isoformat()} -> order "
            f"{self.order_created_at.isoformat()} -> fill "
            f"{self.fill_created_at.isoformat()}: signal->order "
            f"{self.signal_to_order_seconds:.1f}s, order->fill "
            f"{self.order_to_fill_seconds:.1f}s, total {self.total_seconds:.1f}s"
        )


def measure_execution_latency(
    *,
    signal_created_at: datetime,
    order_created_at: datetime,
    fill_created_at: datetime,
) -> ExecutionLatency:
    """Measure the signal -> order -> fill latencies in seconds (SP 4.23).

    Raises:
        PaperExecutionLatencyError: If the timestamps are out of order.
        ValueError: If a timestamp is not UTC-aware.
    """
    _require_utc_aware(signal_created_at, "Signal time")
    _require_utc_aware(order_created_at, "Order time")
    _require_utc_aware(fill_created_at, "Fill time")
    signal_to_order = (order_created_at - signal_created_at).total_seconds()
    order_to_fill = (fill_created_at - order_created_at).total_seconds()
    total = (fill_created_at - signal_created_at).total_seconds()
    return ExecutionLatency(
        signal_created_at=signal_created_at,
        order_created_at=order_created_at,
        fill_created_at=fill_created_at,
        signal_to_order_seconds=signal_to_order,
        order_to_fill_seconds=order_to_fill,
        total_seconds=total,
    )
