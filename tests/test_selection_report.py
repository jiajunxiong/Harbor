"""Selection explainability report tests (MVP 2 / SP 2.32).

Verifies that, for any rebalance date, the report derived from the factor
snapshot (SP 2.28) exposes the candidate pool, the exclusion reasons, the
factor rankings behind each composite score and the final selected symbols,
deterministically and replayably.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.factor_snapshot import FactorSnapshotInput, build_factor_snapshot
from harbor.core.selection_report import SelectionReport, build_selection_report

_AS_OF = date(2026, 3, 31)


def _input(
    market: Market,
    symbol: str,
    scores: dict[str, float | None] | None = None,
    composite: float | None = None,
    rank: int | None = None,
    selected: bool = False,
    reason: str | None = None,
) -> FactorSnapshotInput:
    return FactorSnapshotInput(
        market=market,
        symbol=symbol,
        standardized_scores=scores or {},
        composite_score=composite,
        rank=rank,
        selected=selected,
        exclusion_reason=reason,
    )


def _report(*inputs: FactorSnapshotInput) -> SelectionReport:
    return build_selection_report(build_factor_snapshot(_AS_OF, inputs))


class SelectionReportDerivationTests(unittest.TestCase):
    """Verify candidates / excluded / ranked / selected projections."""

    def test_candidates_excluded_ranked_selected(self) -> None:
        report = _report(
            _input(Market.HK, "0005.HK", {"volatility": 0.3}, 0.75, rank=1, selected=True),
            _input(Market.HK, "0700.HK", {"volatility": 0.2}, 0.70, rank=2),
            _input(Market.HK, "0001.HK", reason="insufficient history (40 < 60)"),
            _input(Market.US, "AAPL", {"volatility": 0.4}, 0.80, rank=1, selected=True),
        )
        self.assertEqual(
            [e.symbol for e in report.candidates()],
            ["0005.HK", "0700.HK", "AAPL"],
        )
        self.assertEqual([e.symbol for e in report.excluded()], ["0001.HK"])
        self.assertEqual(report.excluded()[0].exclusion_reason, "insufficient history (40 < 60)")
        # Ranked best-first within each market: HK 1, HK 2, US 1.
        self.assertEqual(
            [(e.market, e.symbol, e.rank) for e in report.ranked()],
            [
                (Market.HK, "0005.HK", 1),
                (Market.HK, "0700.HK", 2),
                (Market.US, "AAPL", 1),
            ],
        )
        self.assertEqual([e.symbol for e in report.selected()], ["0005.HK", "AAPL"])

    def test_ranked_orders_by_rank_not_symbol(self) -> None:
        report = _report(
            _input(Market.HK, "B.HK", composite=0.9, rank=1, selected=True),
            _input(Market.HK, "A.HK", composite=0.8, rank=2),
        )
        self.assertEqual([e.symbol for e in report.ranked()], ["B.HK", "A.HK"])
        self.assertEqual([e.symbol for e in report.selected()], ["B.HK"])

    def test_for_market_filters_entries(self) -> None:
        report = _report(
            _input(Market.HK, "0005.HK", composite=0.75, rank=1, selected=True),
            _input(Market.US, "AAPL", composite=0.80, rank=1, selected=True),
        )
        self.assertEqual([e.symbol for e in report.for_market(Market.HK)], ["0005.HK"])
        self.assertEqual([e.symbol for e in report.for_market(Market.US)], ["AAPL"])

    def test_empty_snapshot_yields_empty_report(self) -> None:
        report = _report()
        self.assertEqual(report.entries, ())
        self.assertEqual(report.candidates(), ())
        self.assertEqual(report.excluded(), ())
        self.assertEqual(report.selected(), ())

    def test_repeat_build_identical(self) -> None:
        inputs = (
            _input(Market.HK, "B.HK", composite=0.9, rank=1, selected=True),
            _input(Market.HK, "A.HK", composite=0.8, rank=2),
            _input(Market.US, "AAPL", composite=0.8, rank=1, selected=True),
        )
        first = _report(*inputs)
        second = _report(*inputs)
        self.assertEqual(first, second)


class SelectionReportReadableTests(unittest.TestCase):
    """Verify the readable summary exposes every required section."""

    def test_readable_contains_pool_reasons_rankings_and_selection(self) -> None:
        report = _report(
            _input(
                Market.HK,
                "0005.HK",
                {"dividend_yield": 0.9, "volatility": 0.3},
                0.75,
                rank=1,
                selected=True,
            ),
            _input(
                Market.HK,
                "0001.HK",
                reason="insufficient history (40 observations < 60)",
            ),
            _input(Market.US, "AAPL", {"volatility": 0.4}, 0.80, rank=1, selected=True),
        )
        summary = report.readable()
        # Candidate pool.
        self.assertIn("Selection report for 2026-03-31:", summary)
        self.assertIn("HK:", summary)
        self.assertIn("candidates (1): 0005.HK", summary)
        self.assertIn("candidates (1): AAPL", summary)
        # Exclusion reasons.
        self.assertIn("excluded 0001.HK: insufficient history (40 observations < 60)", summary)
        # Factor rankings with scores and composite.
        self.assertIn(
            "1. 0005.HK (composite 0.7500): dividend_yield=0.9000, volatility=0.3000", summary
        )
        self.assertIn("1. AAPL (composite 0.8000): volatility=0.4000", summary)
        # Final selection per market and overall.
        self.assertIn("selected (1): 0005.HK", summary)
        self.assertIn("selected (1): AAPL", summary)
        self.assertIn("selected (2): 0005.HK, AAPL", summary)

    def test_readable_marks_none_composite_and_no_scores(self) -> None:
        report = _report(_input(Market.HK, "0700.HK"))
        summary = report.readable()
        self.assertIn("0700.HK", summary)
        self.assertIn("candidates (1): 0700.HK", summary)


if __name__ == "__main__":
    unittest.main()
