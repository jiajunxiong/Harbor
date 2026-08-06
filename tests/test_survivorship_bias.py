"""Survivorship-bias tests (MVP 2 / SP 2.30).

Verifies that delisted or otherwise invalidated mock symbols participate while
their membership window is valid and cannot participate after it ends
(SP 2.10 / SP 2.23):

- a symbol delisted before a decision date is excluded from the active pool and
  from the candidate set with a readable reason;
- a symbol that delists only AFTER a decision date still participates on that
  date and is excluded only afterwards;
- a symbol listed only after a decision date cannot participate before it;
- a symbol with an unknown listing window never participates;
- the survivorship-bias risk flag is raised when the source does not guarantee
  historical constituents, no membership is active, or some membership lacks
  an inclusion date.

Running the candidate filter and selector at several decision dates confirms
that a delisted symbol is selectable while active and drops out of selection
after delisting — the survivorship effect the acceptance criterion targets.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.candidate_filter import (
    CandidateFilterConfig,
    CandidateFilterResult,
    CandidateInputs,
    filter_candidates,
)
from harbor.core.market_selector import SelectionResult, select_candidates
from harbor.core.stock_pool import StockPool, StockPoolMembership, evaluate_stock_pool

_BEFORE = date(2025, 6, 30)  # GONE and GONELATER still listed
_MID = date(2026, 3, 31)  # GONE delisted, LATE listed, GONELATER still listed
_AFTER = date(2026, 9, 30)  # GONELATER also delisted


def _membership(
    symbol: str,
    effective: date | None,
    expiry: date | None = None,
) -> StockPoolMembership:
    return StockPoolMembership(Market.HK, symbol, effective, expiry, "mock")


def _healthy(symbol: str) -> CandidateInputs:
    """Return tradability inputs that pass every filter check."""
    return CandidateInputs(
        observation_count=100,
        average_turnover=1.0e6,
        suspension_ratio=0.0,
        data_complete=True,
    )


def _universe() -> tuple[StockPoolMembership, ...]:
    """Return the mock universe with a mix of validity windows."""
    return (
        _membership("STAY.HK", date(2000, 1, 1)),
        _membership("GONE.HK", date(2000, 1, 1), date(2025, 12, 31)),
        _membership("LATE.HK", date(2026, 1, 1)),
        _membership("UNK.HK", None),
        _membership("GONELATER.HK", date(2000, 1, 1), date(2026, 6, 30)),
    )


def _run(as_of: date) -> tuple[StockPool, CandidateFilterResult, SelectionResult]:
    """Run the pool, candidate filter and selector on ``as_of``."""
    memberships = _universe()
    pool = evaluate_stock_pool(
        Market.HK,
        as_of,
        memberships,
        "mock",
        historical_known=True,
    )
    inputs = {membership.symbol: _healthy(membership.symbol) for membership in memberships}
    filtered = filter_candidates(
        Market.HK,
        as_of,
        memberships,
        inputs,
        config=CandidateFilterConfig(),
    )
    scores = {symbol: (0.95 if symbol == "GONE.HK" else 0.9) for symbol in filtered.candidates}
    selection = select_candidates(Market.HK, as_of, scores, target_count=2)
    return pool, filtered, selection


def _excluded_reason(
    filtered: CandidateFilterResult,
    symbol: str,
) -> str:
    """Return the exclusion reason for a symbol (must be present)."""
    for outcome in filtered.excluded:
        if outcome.symbol == symbol:
            return outcome.reason or ""
    raise AssertionError(f"{symbol} was not excluded")


class SurvivorshipBiasTests(unittest.TestCase):
    """Verify participation during validity and exclusion after it."""

    def test_stay_listed_participates_on_all_dates(self) -> None:
        for as_of in (_BEFORE, _MID, _AFTER):
            pool, filtered, _ = _run(as_of)
            self.assertIn("STAY.HK", pool.symbols, msg=as_of.isoformat())
            self.assertIn("STAY.HK", filtered.candidates, msg=as_of.isoformat())

    def test_delisted_symbol_participates_then_excluded(self) -> None:
        before_pool, before_filtered, _ = _run(_BEFORE)
        self.assertIn("GONE.HK", before_pool.symbols)
        self.assertIn("GONE.HK", before_filtered.candidates)

        mid_pool, mid_filtered, _ = _run(_MID)
        self.assertNotIn("GONE.HK", mid_pool.symbols)
        self.assertNotIn("GONE.HK", mid_filtered.candidates)
        self.assertIn("delisted before the as-of date", _excluded_reason(mid_filtered, "GONE.HK"))

    def test_delisting_after_decision_date_keeps_participation(self) -> None:
        mid_pool, mid_filtered, _ = _run(_MID)
        self.assertIn("GONELATER.HK", mid_pool.symbols)
        self.assertIn("GONELATER.HK", mid_filtered.candidates)

        after_pool, after_filtered, _ = _run(_AFTER)
        self.assertNotIn("GONELATER.HK", after_pool.symbols)
        self.assertNotIn("GONELATER.HK", after_filtered.candidates)
        self.assertIn(
            "delisted before the as-of date", _excluded_reason(after_filtered, "GONELATER.HK")
        )

    def test_not_yet_listed_symbol_cannot_participate_before_listing(self) -> None:
        before_pool, before_filtered, _ = _run(_BEFORE)
        self.assertNotIn("LATE.HK", before_pool.symbols)
        self.assertNotIn("LATE.HK", before_filtered.candidates)
        self.assertIn(
            "not yet listed on the as-of date", _excluded_reason(before_filtered, "LATE.HK")
        )

        mid_pool, mid_filtered, _ = _run(_MID)
        self.assertIn("LATE.HK", mid_pool.symbols)
        self.assertIn("LATE.HK", mid_filtered.candidates)

    def test_unknown_window_symbol_never_participates(self) -> None:
        for as_of in (_BEFORE, _MID, _AFTER):
            pool, filtered, _ = _run(as_of)
            self.assertNotIn("UNK.HK", pool.symbols, msg=as_of.isoformat())
            self.assertNotIn("UNK.HK", filtered.candidates, msg=as_of.isoformat())
            self.assertIn(
                "listing window unknown on the as-of date",
                _excluded_reason(filtered, "UNK.HK"),
            )

    def test_selection_drops_delisted_symbol_after_delisting(self) -> None:
        _, _, before_selection = _run(_BEFORE)
        self.assertIn("GONE.HK", before_selection.selected)

        _, _, mid_selection = _run(_MID)
        self.assertNotIn("GONE.HK", mid_selection.selected)
        self.assertNotIn("GONE.HK", mid_selection.candidates)

    def test_active_pool_symbols_match_expected_windows(self) -> None:
        expected = {
            _BEFORE: {"STAY.HK", "GONE.HK", "GONELATER.HK"},
            _MID: {"STAY.HK", "LATE.HK", "GONELATER.HK"},
            _AFTER: {"STAY.HK", "LATE.HK"},
        }
        for as_of, symbols in expected.items():
            pool, _, _ = _run(as_of)
            self.assertEqual(set(pool.symbols), symbols, msg=as_of.isoformat())


class SurvivorshipRiskFlagTests(unittest.TestCase):
    """Verify the survivorship-bias risk marking (SP 2.10)."""

    def test_unknown_history_marks_risk(self) -> None:
        pool = evaluate_stock_pool(
            Market.HK,
            _MID,
            _universe(),
            "mock",
            historical_known=False,
        )
        self.assertTrue(pool.survivorship_bias_risk)
        self.assertIn("source does not guarantee historical constituents", pool.risk_reason or "")

    def test_known_history_with_unknown_window_marks_risk(self) -> None:
        pool = evaluate_stock_pool(
            Market.HK,
            _MID,
            _universe(),
            "mock",
            historical_known=True,
        )
        self.assertTrue(pool.survivorship_bias_risk)
        self.assertIn("inclusion (effective) date", pool.risk_reason or "")

    def test_no_active_memberships_marks_risk(self) -> None:
        pool = evaluate_stock_pool(
            Market.HK,
            date(1999, 1, 1),
            _universe(),
            "mock",
            historical_known=True,
        )
        self.assertTrue(pool.survivorship_bias_risk)
        self.assertIn("no memberships are active", pool.risk_reason or "")

    def test_complete_known_history_clears_risk(self) -> None:
        memberships = (
            _membership("STAY.HK", date(2000, 1, 1)),
            _membership("GONE.HK", date(2000, 1, 1), date(2025, 12, 31)),
        )
        pool = evaluate_stock_pool(
            Market.HK,
            _MID,
            memberships,
            "mock",
            historical_known=True,
        )
        self.assertFalse(pool.survivorship_bias_risk)
        self.assertIsNone(pool.risk_reason)


if __name__ == "__main__":
    unittest.main()
