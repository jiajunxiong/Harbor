"""Research assumption snapshot tests (MVP 4 / SP 4.69).

Verifies that the OOS research assumptions (slippage, cost, spread,
participation, fill rule, latency) are recorded versioned and replay-identical.
"""

import unittest

from harbor.core.backtest_config import FillRule
from harbor.core.backtest_domain import Market
from harbor.core.paper_assumption_snapshot import (
    ResearchAssumption,
    ResearchAssumptionError,
    ResearchAssumptionSnapshot,
    build_research_assumption_snapshot,
    research_assumption_fingerprint,
)


def _assumption(market: Market = Market.HK, **overrides: float) -> ResearchAssumption:
    values = {
        "slippage_bps": 10.0,
        "cost_bps": 8.0,
        "spread_bps": 5.0,
        "participation_rate": 0.02,
        "execution_latency_seconds": 5.0,
    }
    values.update(overrides)
    return ResearchAssumption(
        market=market,
        fill_rule=FillRule.CLOSE,
        **values,
    )


def _snapshot(*assumptions: ResearchAssumption) -> ResearchAssumptionSnapshot:
    return build_research_assumption_snapshot(
        version="assumption-1.0",
        source_run_id="oos-1",
        dataset_fingerprint="dataset-abc",
        assumptions=assumptions,
    )


class ResearchAssumptionTests(unittest.TestCase):
    """The OOS assumption value object (SP 4.69)."""

    def test_valid_assumption(self) -> None:
        assumption = _assumption()
        self.assertEqual(assumption.slippage_bps, 10.0)
        self.assertIn("participation 2.00%", assumption.readable())

    def test_negative_costs_rejected(self) -> None:
        with self.assertRaises(ResearchAssumptionError):
            _assumption(slippage_bps=-1.0)
        with self.assertRaises(ResearchAssumptionError):
            _assumption(cost_bps=-1.0)
        with self.assertRaises(ResearchAssumptionError):
            _assumption(spread_bps=-1.0)

    def test_participation_and_latency_validated(self) -> None:
        with self.assertRaises(ResearchAssumptionError):
            _assumption(participation_rate=0.0)
        with self.assertRaises(ResearchAssumptionError):
            _assumption(participation_rate=1.5)
        with self.assertRaises(ResearchAssumptionError):
            _assumption(execution_latency_seconds=-1.0)

    def test_reference_price_positive(self) -> None:
        with self.assertRaises(ResearchAssumptionError):
            ResearchAssumption(
                market=Market.HK,
                slippage_bps=1,
                cost_bps=1,
                spread_bps=1,
                participation_rate=0.1,
                fill_rule=FillRule.CLOSE,
                execution_latency_seconds=1,
                reference_price=0.0,
            )


class ResearchAssumptionSnapshotTests(unittest.TestCase):
    """The versioned snapshot (SP 4.69)."""

    def test_build_and_lookup(self) -> None:
        snapshot = _snapshot(_assumption(Market.HK), _assumption(Market.US))
        self.assertEqual(snapshot.assumption_for(Market.HK).market, Market.HK)
        self.assertEqual(snapshot.assumption_for(Market.US).market, Market.US)
        hk_only = _snapshot(_assumption(Market.HK))
        self.assertIsNone(hk_only.assumption_for(Market.US))

    def test_duplicate_market_rejected(self) -> None:
        with self.assertRaises(ResearchAssumptionError):
            _snapshot(_assumption(Market.HK), _assumption(Market.HK))

    def test_required_fields(self) -> None:
        with self.assertRaises(ResearchAssumptionError):
            build_research_assumption_snapshot(
                version="",
                source_run_id="oos-1",
                dataset_fingerprint="d",
                assumptions=(),
            )
        with self.assertRaises(ResearchAssumptionError):
            build_research_assumption_snapshot(
                version="v1",
                source_run_id="",
                dataset_fingerprint="d",
                assumptions=(),
            )

    def test_fingerprint_replayable_and_source_excluded(self) -> None:
        first = _snapshot(_assumption(Market.HK))
        second = build_research_assumption_snapshot(
            version="assumption-1.0",
            source_run_id="oos-2",
            dataset_fingerprint="dataset-abc",
            assumptions=(_assumption(Market.HK),),
        )
        self.assertEqual(first.fingerprint(), second.fingerprint())
        self.assertEqual(research_assumption_fingerprint(first), first.fingerprint())
        changed = _snapshot(_assumption(Market.HK, slippage_bps=20.0))
        self.assertNotEqual(first.fingerprint(), changed.fingerprint())
