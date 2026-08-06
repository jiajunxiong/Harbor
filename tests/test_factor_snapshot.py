"""Factor snapshot tests (MVP 2 / SP 2.28).

Verifies the rebalance factor snapshot model: normalization of raw values,
availability dates and standardized scores into canonical key-sorted tuples,
deterministic entry ordering, preservation of composite score / rank /
selection / exclusion reason, input validation and the readable summary.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.factor_snapshot import (
    FactorSnapshotInput,
    build_factor_snapshot,
)

_AS_OF = date(2026, 3, 31)


def _hk_input(symbol: str = "0005.HK", **overrides: object) -> FactorSnapshotInput:
    """Return a representative HK snapshot input with per-field overrides."""
    defaults: dict[str, object] = {
        "market": Market.HK,
        "symbol": symbol,
        "raw_values": {"dividend_yield": 0.05, "volatility": 0.2},
        "availability_dates": {"price": date(2026, 3, 30), "dividend": date(2026, 3, 28)},
        "standardized_scores": {"dividend_yield": 0.9, "volatility": 0.3},
        "composite_score": 0.75,
        "rank": 1,
        "selected": True,
        "exclusion_reason": None,
    }
    defaults.update(overrides)
    return FactorSnapshotInput(**defaults)


class FactorSnapshotBuildTests(unittest.TestCase):
    """Verify the snapshot builder normalizes and orders deterministically."""

    def test_normalizes_mappings_to_sorted_tuples(self) -> None:
        snapshot = build_factor_snapshot(_AS_OF, [_hk_input()])
        entry = snapshot.entries[0]
        self.assertEqual(
            entry.raw_values,
            (("dividend_yield", 0.05), ("volatility", 0.2)),
        )
        self.assertEqual(
            entry.availability_dates,
            (("dividend", date(2026, 3, 28)), ("price", date(2026, 3, 30))),
        )
        self.assertEqual(
            entry.standardized_scores,
            (("dividend_yield", 0.9), ("volatility", 0.3)),
        )

    def test_orders_entries_by_market_then_symbol(self) -> None:
        snapshot = build_factor_snapshot(
            _AS_OF,
            [
                _hk_input("0700.HK"),
                FactorSnapshotInput(
                    market=Market.US,
                    symbol="AAPL",
                    composite_score=0.8,
                    rank=1,
                    selected=True,
                ),
                _hk_input("0005.HK"),
            ],
        )
        self.assertEqual(
            [(e.market.value, e.symbol) for e in snapshot.entries],
            [("HK", "0005.HK"), ("HK", "0700.HK"), ("US", "AAPL")],
        )

    def test_preserves_score_rank_selection_and_exclusion(self) -> None:
        snapshot = build_factor_snapshot(
            _AS_OF,
            [
                _hk_input("0005.HK"),
                _hk_input(
                    "0700.HK",
                    composite_score=None,
                    rank=None,
                    selected=False,
                    exclusion_reason="insufficient history (40 observations < 60)",
                ),
            ],
        )
        accepted = snapshot.entries[0]
        self.assertEqual(accepted.composite_score, 0.75)
        self.assertEqual(accepted.rank, 1)
        self.assertTrue(accepted.selected)
        self.assertIsNone(accepted.exclusion_reason)
        excluded = snapshot.entries[1]
        self.assertIsNone(excluded.composite_score)
        self.assertIsNone(excluded.rank)
        self.assertFalse(excluded.selected)
        self.assertIn("insufficient history", excluded.exclusion_reason or "")

    def test_none_availability_dates_allowed(self) -> None:
        snapshot = build_factor_snapshot(_AS_OF, [_hk_input(availability_dates={})])
        self.assertEqual(snapshot.entries[0].availability_dates, ())

    def test_for_market_filters_entries(self) -> None:
        snapshot = build_factor_snapshot(
            _AS_OF,
            [
                _hk_input("0005.HK"),
                FactorSnapshotInput(
                    market=Market.US,
                    symbol="AAPL",
                    composite_score=0.8,
                    rank=1,
                    selected=True,
                ),
            ],
        )
        hk_entries = snapshot.for_market(Market.HK)
        us_entries = snapshot.for_market(Market.US)
        self.assertEqual([e.symbol for e in hk_entries], ["0005.HK"])
        self.assertEqual([e.symbol for e in us_entries], ["AAPL"])

    def test_repeat_calls_identical(self) -> None:
        inputs = [
            _hk_input("0700.HK"),
            _hk_input("0005.HK"),
            FactorSnapshotInput(market=Market.US, symbol="AAPL"),
        ]
        first = build_factor_snapshot(_AS_OF, inputs)
        second = build_factor_snapshot(_AS_OF, inputs)
        self.assertEqual(first, second)


class FactorSnapshotValidationTests(unittest.TestCase):
    """Verify the snapshot input validation (SP 2.28)."""

    def test_rejects_empty_symbol(self) -> None:
        with self.assertRaisesRegex(ValueError, "symbol"):
            _hk_input(symbol="")

    def test_rejects_non_positive_rank(self) -> None:
        with self.assertRaisesRegex(ValueError, "rank"):
            _hk_input(rank=0)
        with self.assertRaisesRegex(ValueError, "rank"):
            _hk_input(rank=-1)

    def test_rejects_selected_without_rank(self) -> None:
        with self.assertRaisesRegex(ValueError, "rank"):
            _hk_input(rank=None, selected=True)

    def test_rejects_duplicate_symbol_per_market(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate snapshot symbol"):
            build_factor_snapshot(_AS_OF, [_hk_input("0005.HK"), _hk_input("0005.HK")])


class FactorSnapshotReadableTests(unittest.TestCase):
    """Verify the readable summary (SP 2.28)."""

    def test_readable_summary(self) -> None:
        snapshot = build_factor_snapshot(
            _AS_OF,
            [
                _hk_input("0005.HK"),
                _hk_input(
                    "0700.HK",
                    raw_values={},
                    availability_dates={},
                    standardized_scores={},
                    composite_score=None,
                    rank=None,
                    selected=False,
                    exclusion_reason="incomplete data",
                ),
            ],
        )
        summary = snapshot.readable()
        self.assertIn("Factor snapshot for 2026-03-31:", summary)
        self.assertIn("HK/0005.HK", summary)
        self.assertIn("raw dividend_yield=0.0500, volatility=0.2000", summary)
        self.assertIn("available dividend@2026-03-28, price@2026-03-30", summary)
        self.assertIn("standardized dividend_yield=0.9000, volatility=0.3000", summary)
        self.assertIn("composite 0.7500; rank 1 (selected)", summary)
        self.assertIn("HK/0700.HK", summary)
        self.assertIn("composite n/a; rank n/a (not ranked)", summary)
        self.assertIn("excluded: incomplete data", summary)


if __name__ == "__main__":
    unittest.main()
