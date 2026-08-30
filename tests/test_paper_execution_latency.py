"""Paper execution latency tests (MVP 4 / SP 4.23).

Verifies that the signal -> order -> fill timestamps are recorded and their
per-leg latencies measured, and that out-of-order or naive series are
rejected.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.paper_execution_latency import (
    ExecutionLatency,
    PaperExecutionLatencyError,
    measure_execution_latency,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class MeasureExecutionLatencyTests(unittest.TestCase):
    """The latency measurement (SP 4.23)."""

    def test_valid_series(self) -> None:
        latency = measure_execution_latency(
            signal_created_at=_utc_at(2026, 1, 2, 8),
            order_created_at=_utc_at(2026, 1, 2, 9),
            fill_created_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertIsInstance(latency, ExecutionLatency)
        self.assertEqual(latency.signal_to_order_seconds, 3600.0)
        self.assertEqual(latency.order_to_fill_seconds, 1800.0)
        self.assertEqual(latency.total_seconds, 5400.0)
        self.assertEqual(
            latency.total_seconds, latency.signal_to_order_seconds + latency.order_to_fill_seconds
        )

    def test_zero_duration(self) -> None:
        at = _utc_at(2026, 1, 2, 9)
        latency = measure_execution_latency(
            signal_created_at=at, order_created_at=at, fill_created_at=at
        )
        self.assertEqual(latency.total_seconds, 0.0)

    def test_out_of_order_rejected(self) -> None:
        with self.assertRaises(PaperExecutionLatencyError):
            measure_execution_latency(
                signal_created_at=_utc_at(2026, 1, 2, 9),
                order_created_at=_utc_at(2026, 1, 2, 8),
                fill_created_at=_utc_at(2026, 1, 2, 9, 30),
            )
        with self.assertRaises(PaperExecutionLatencyError):
            measure_execution_latency(
                signal_created_at=_utc_at(2026, 1, 2, 8),
                order_created_at=_utc_at(2026, 1, 2, 9),
                fill_created_at=_utc_at(2026, 1, 2, 8, 30),
            )

    def test_naive_timestamp_rejected(self) -> None:
        with self.assertRaises(ValueError):
            measure_execution_latency(
                signal_created_at=datetime(2026, 1, 2, 8),
                order_created_at=_utc_at(2026, 1, 2, 9),
                fill_created_at=_utc_at(2026, 1, 2, 9, 30),
            )

    def test_readable(self) -> None:
        latency = measure_execution_latency(
            signal_created_at=_utc_at(2026, 1, 2, 8),
            order_created_at=_utc_at(2026, 1, 2, 9),
            fill_created_at=_utc_at(2026, 1, 2, 9, 30),
        )
        self.assertIn("signal", latency.readable())
        self.assertIn("5400.0s", latency.readable())
