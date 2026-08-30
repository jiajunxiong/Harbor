"""Paper signal model tests (MVP 4 / SP 4.14).

Verifies that a strategy output is normalized into a validated signal
intention with its source run/version, and that empty, negative or
out-of-range target weights are rejected rather than silently normalized.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Market
from harbor.core.paper_domain import SignalIntention
from harbor.core.paper_signal import MarketSignal, PaperSignalError, build_signal_intention


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _build(**overrides: object) -> SignalIntention:
    """Build a signal intention with overridable kwargs."""
    fields: dict[str, object] = {
        "intention_id": "sig-1",
        "paper_run_id": "paper-1",
        "strategy": "mvp3-qualified",
        "strategy_version": "1.0.0",
        "market": Market.HK,
        "rebalance_date": date(2026, 1, 2),
        "target_weights": (("0001.HK", 0.5), ("0002.HK", 0.5)),
        "source_run_id": "mvp3-run-1",
        "created_at": _utc_at(2026, 1, 1, 12),
    }
    fields.update(overrides)
    return build_signal_intention(**fields)  # type: ignore[arg-type]


class BuildSignalIntentionTests(unittest.TestCase):
    """Normalizing a strategy output (SP 4.14)."""

    def test_valid_signal(self) -> None:
        intention = _build()
        self.assertEqual(intention.intention_id, "sig-1")
        self.assertEqual(intention.market, Market.HK)
        self.assertEqual(intention.rebalance_date, date(2026, 1, 2))
        self.assertEqual(intention.source_run_id, "mvp3-run-1")
        self.assertEqual(intention.weight_of("0001.HK"), 0.5)

    def test_partial_weight_allowed(self) -> None:
        intention = _build(target_weights=(("0001.HK", 0.3),))
        self.assertEqual(intention.weight_of("0001.HK"), 0.3)

    def test_empty_weights_rejected(self) -> None:
        with self.assertRaises(PaperSignalError):
            _build(target_weights=())

    def test_negative_weight_rejected(self) -> None:
        with self.assertRaises(PaperSignalError):
            _build(target_weights=(("0001.HK", -0.1),))

    def test_zero_total_rejected(self) -> None:
        with self.assertRaises(PaperSignalError):
            _build(target_weights=(("0001.HK", 0.0), ("0002.HK", 0.0)))

    def test_overweight_rejected(self) -> None:
        with self.assertRaises(PaperSignalError):
            _build(target_weights=(("0001.HK", 0.8), ("0002.HK", 0.4)))

    def test_empty_identity_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _build(intention_id="")
        with self.assertRaises(ValueError):
            _build(strategy_version="")

    def test_default_timestamp_is_utc(self) -> None:
        intention = build_signal_intention(
            intention_id="sig-2",
            paper_run_id="paper-1",
            strategy="s",
            strategy_version="1",
            market=Market.HK,
            rebalance_date=date(2026, 1, 2),
            target_weights=(("0001.HK", 1.0),),
        )
        self.assertIsNotNone(intention.created_at.utcoffset())
        self.assertEqual(intention.created_at.utcoffset().total_seconds(), 0.0)


class MarketSignalTests(unittest.TestCase):
    """The signal spec value (SP 4.14)."""

    def test_readable(self) -> None:
        spec = MarketSignal(Market.HK, date(2026, 1, 2), (("0001.HK", 0.5),))
        self.assertIn("0001.HK:0.5", spec.readable())
