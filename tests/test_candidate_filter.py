"""Single-market candidate filter tests (MVP 2 / SP 2.23).

Verifies exclusion for delisted / not-yet-listed / unknown-window symbols,
insufficient history, insufficient liquidity, excessive suspension and
incomplete data, plus the deterministic first-failing-check ordering and
readable output.
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
from harbor.core.stock_pool import StockPoolMembership

_AS_OF = date(2026, 3, 31)


def _membership(
    symbol: str,
    effective: date | None = date(2000, 1, 1),
    expiry: date | None = None,
) -> StockPoolMembership:
    return StockPoolMembership(Market.HK, symbol, effective, expiry, "test")


def _inputs(
    observation_count: int = 100,
    average_turnover: float | None = 1.0e6,
    suspension_ratio: float | None = 0.0,
    data_complete: bool = True,
) -> CandidateInputs:
    return CandidateInputs(
        observation_count=observation_count,
        average_turnover=average_turnover,
        suspension_ratio=suspension_ratio,
        data_complete=data_complete,
    )


class CandidateFilterConfigTests(unittest.TestCase):
    """Verify filter configuration validation (SP 2.23)."""

    def test_rejects_negative_min_history(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_history_observations"):
            CandidateFilterConfig(min_history_observations=-1)

    def test_rejects_negative_min_turnover(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_average_turnover"):
            CandidateFilterConfig(min_average_turnover=-1.0)

    def test_rejects_out_of_range_suspension_ratio(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_suspension_ratio"):
            CandidateFilterConfig(max_suspension_ratio=1.1)
        with self.assertRaisesRegex(ValueError, "max_suspension_ratio"):
            CandidateFilterConfig(max_suspension_ratio=-0.1)


class MembershipWindowTests(unittest.TestCase):
    """Verify delisted / not-yet-listed / unknown-window exclusions (SP 2.23)."""

    def test_delisted_symbol_excluded(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("OLD.HK", expiry=date(2025, 12, 31)),),
            {},
        )
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.excluded[0].symbol, "OLD.HK")
        self.assertIn("delisted", result.excluded[0].reason or "")

    def test_not_yet_listed_symbol_excluded(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("NEW.HK", effective=date(2026, 4, 1)),),
            {},
        )
        self.assertEqual(result.candidates, ())
        self.assertIn("not yet listed", result.excluded[0].reason or "")

    def test_unknown_window_symbol_excluded(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("UNK.HK", effective=None),),
            {},
        )
        self.assertEqual(result.candidates, ())
        self.assertIn("listing window unknown", result.excluded[0].reason or "")


class TradabilityFilterTests(unittest.TestCase):
    """Verify history, liquidity, suspension and completeness exclusions."""

    def test_healthy_symbol_accepted(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"),),
            {"0005.HK": _inputs()},
        )
        self.assertEqual(result.candidates, ("0005.HK",))
        self.assertEqual(result.excluded, ())

    def test_insufficient_history_excluded(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"),),
            {"0005.HK": _inputs(observation_count=10)},
            config=CandidateFilterConfig(min_history_observations=60),
        )
        self.assertEqual(result.candidates, ())
        self.assertIn("insufficient history", result.excluded[0].reason or "")

    def test_unassessable_turnover_excluded(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"),),
            {"0005.HK": _inputs(average_turnover=None)},
        )
        self.assertEqual(result.candidates, ())
        self.assertIn("not assessable", result.excluded[0].reason or "")

    def test_low_turnover_excluded(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"),),
            {"0005.HK": _inputs(average_turnover=100.0)},
            config=CandidateFilterConfig(min_average_turnover=1_000.0),
        )
        self.assertEqual(result.candidates, ())
        self.assertIn("insufficient liquidity", result.excluded[0].reason or "")

    def test_excessive_suspension_excluded(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"),),
            {"0005.HK": _inputs(suspension_ratio=0.8)},
            config=CandidateFilterConfig(max_suspension_ratio=0.3),
        )
        self.assertEqual(result.candidates, ())
        self.assertIn("suspended too long", result.excluded[0].reason or "")

    def test_suspension_within_limit_accepted(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"),),
            {"0005.HK": _inputs(suspension_ratio=0.2)},
            config=CandidateFilterConfig(max_suspension_ratio=0.3),
        )
        self.assertEqual(result.candidates, ("0005.HK",))

    def test_incomplete_data_excluded(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"),),
            {"0005.HK": _inputs(data_complete=False)},
        )
        self.assertEqual(result.candidates, ())
        self.assertIn("incomplete data", result.excluded[0].reason or "")

    def test_active_symbol_without_inputs_excluded(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"),),
            {},
        )
        self.assertEqual(result.candidates, ())
        self.assertIn("incomplete data", result.excluded[0].reason or "")


class PriorityAndReadableTests(unittest.TestCase):
    """Verify check ordering, deterministic output and readability (SP 2.23)."""

    def test_first_failing_check_wins(self) -> None:
        # Insufficient history and unassessable turnover both apply; history wins.
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"),),
            {"0005.HK": _inputs(observation_count=5, average_turnover=None)},
            config=CandidateFilterConfig(min_history_observations=60),
        )
        self.assertEqual(result.excluded[0].reason, "insufficient history (5 observations < 60)")

    def test_candidates_sorted_deterministically(self) -> None:
        memberships = (
            _membership("B.HK"),
            _membership("A.HK"),
            _membership("C.HK"),
        )
        inputs = {"B.HK": _inputs(), "A.HK": _inputs(), "C.HK": _inputs()}
        result = filter_candidates(Market.HK, _AS_OF, memberships, inputs)
        self.assertEqual(result.candidates, ("A.HK", "B.HK", "C.HK"))

    def test_readable_summary(self) -> None:
        result = filter_candidates(
            Market.HK,
            _AS_OF,
            (_membership("0005.HK"), _membership("OLD.HK", expiry=date(2025, 1, 1))),
            {"0005.HK": _inputs()},
        )
        summary = result.readable()
        self.assertIn("accepted (1): 0005.HK", summary)
        self.assertIn("- EXCLUDED OLD.HK: delisted before the as-of date", summary)

    def test_result_is_immutable_dataclass(self) -> None:
        result = filter_candidates(Market.HK, _AS_OF, (), {})
        self.assertIsInstance(result, CandidateFilterResult)
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.excluded, ())


if __name__ == "__main__":
    unittest.main()
