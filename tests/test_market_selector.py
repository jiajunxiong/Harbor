"""Per-market selector tests (MVP 2 / SP 2.25 HK, SP 2.26 US).

Verifies target-count selection by composite score, deterministic tie breaking,
unrankable (None-score) handling, input snapshot preservation, ranking detail
and the market-fixed HK/US entry points.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.market_selector import (
    SelectionResult,
    select_candidates,
    select_hk_candidates,
    select_us_candidates,
)

_AS_OF = date(2026, 3, 31)


class SelectionTests(unittest.TestCase):
    """Verify selection behavior (SP 2.25/2.26)."""

    def test_selects_top_scores(self) -> None:
        result = select_candidates(
            Market.HK,
            _AS_OF,
            {"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6},
            target_count=2,
        )
        self.assertEqual(result.selected, ("A", "B"))

    def test_ranks_best_first_with_detail(self) -> None:
        result = select_candidates(
            Market.HK,
            _AS_OF,
            {"A": 0.9, "B": 0.8, "C": 0.7},
            target_count=1,
        )
        self.assertEqual([r.symbol for r in result.rankings], ["A", "B", "C"])
        self.assertEqual([r.rank for r in result.rankings], [1, 2, 3])
        self.assertEqual([r.selected for r in result.rankings], [True, False, False])

    def test_ties_broken_by_symbol_ascending(self) -> None:
        result = select_candidates(
            Market.US,
            _AS_OF,
            {"B": 0.5, "A": 0.5, "C": 0.5},
            target_count=1,
        )
        self.assertEqual(result.selected, ("A",))

    def test_more_scored_than_needed(self) -> None:
        result = select_candidates(
            Market.HK,
            _AS_OF,
            {"A": 0.9, "B": 0.8, "C": 0.7},
            target_count=5,
        )
        self.assertEqual(result.selected, ("A", "B", "C"))

    def test_none_scores_excluded_from_ranking_and_selection(self) -> None:
        result = select_candidates(
            Market.HK,
            _AS_OF,
            {"A": 0.9, "B": None, "C": 0.8},
            target_count=2,
        )
        self.assertEqual(result.selected, ("A", "C"))
        self.assertEqual([r.symbol for r in result.rankings], ["A", "C"])
        # The unrankable symbol stays in the input snapshot.
        self.assertEqual(result.candidates, ("A", "B", "C"))

    def test_candidates_snapshot_is_sorted(self) -> None:
        result = select_candidates(
            Market.HK,
            _AS_OF,
            {"C": 0.7, "A": 0.9, "B": 0.8},
            target_count=3,
        )
        self.assertEqual(result.candidates, ("A", "B", "C"))

    def test_target_count_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "target_count"):
            select_candidates(Market.HK, _AS_OF, {}, target_count=0)
        with self.assertRaisesRegex(ValueError, "target_count"):
            select_candidates(Market.HK, _AS_OF, {}, target_count=-1)

    def test_empty_candidates(self) -> None:
        result = select_candidates(Market.HK, _AS_OF, {}, target_count=5)
        self.assertEqual(result.selected, ())
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.rankings, ())


class MarketEntryPointTests(unittest.TestCase):
    """Verify the HK (SP 2.25) and US (SP 2.26) entry points."""

    def test_hk_selector_fixes_market(self) -> None:
        result = select_hk_candidates(
            _AS_OF,
            {"0005.HK": 0.9, "0700.HK": 0.8},
            target_count=1,
        )
        self.assertIsInstance(result, SelectionResult)
        self.assertEqual(result.market, Market.HK)
        self.assertEqual(result.selected, ("0005.HK",))

    def test_us_selector_fixes_market(self) -> None:
        result = select_us_candidates(
            _AS_OF,
            {"AAPL": 0.9, "MSFT": 0.8},
            target_count=1,
        )
        self.assertEqual(result.market, Market.US)
        self.assertEqual(result.selected, ("AAPL",))


class ReadableTests(unittest.TestCase):
    """Verify the readable summary (SP 2.25/2.26)."""

    def test_readable_summary(self) -> None:
        result = select_hk_candidates(
            _AS_OF,
            {"0005.HK": 0.9, "0700.HK": 0.8, "0001.HK": None},
            target_count=1,
        )
        summary = result.readable()
        self.assertIn("Selection for HK on 2026-03-31 (target 1):", summary)
        self.assertIn("selected (1): 0005.HK", summary)
        self.assertIn("1. 0005.HK (0.9000) selected", summary)
        self.assertIn("2. 0700.HK (0.8000) not selected", summary)


class DeterminismTests(unittest.TestCase):
    """Verify the selection is replayable (SP 2.25/2.26)."""

    def test_repeat_calls_identical(self) -> None:
        scores = {"A": 0.9, "B": None, "C": 0.9, "D": 0.7}
        first = select_candidates(Market.HK, _AS_OF, scores, target_count=2)
        second = select_candidates(Market.HK, _AS_OF, scores, target_count=2)
        self.assertEqual(first.selected, second.selected)
        self.assertEqual(first.rankings, second.rankings)


if __name__ == "__main__":
    unittest.main()
