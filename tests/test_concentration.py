"""Concentration constraint tests (MVP 2 / SP 2.35).

Verifies the single-stock cap, single-market cap and minimum-cash adjustments,
and that constraint conflicts are reported whenever the target weights violate
a limit.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import MarketQuota, RiskConfig
from harbor.core.backtest_domain import Currency, Market
from harbor.core.concentration import (
    ConstraintKind,
    apply_concentration_constraints,
)
from harbor.core.cross_market_merge import MergedSelection, merge_selections
from harbor.core.market_selector import SelectionResult, select_candidates
from harbor.core.target_weight import (
    TargetWeightConfig,
    TargetWeightResult,
    WeightingMethod,
    compute_target_weights,
)

_AS_OF = date(2026, 3, 31)


def _selection(market: Market, symbols: tuple[str, ...]) -> SelectionResult:
    """Select every symbol; an empty set yields an empty selection."""
    if not symbols:
        return select_candidates(market, _AS_OF, {}, target_count=3)
    scores = {symbol: 1.0 - index * 0.01 for index, symbol in enumerate(symbols)}
    return select_candidates(market, _AS_OF, scores, target_count=len(symbols))


def _merged(
    hk_symbols: tuple[str, ...],
    us_symbols: tuple[str, ...],
) -> MergedSelection:
    hk = _selection(Market.HK, hk_symbols)
    us = _selection(Market.US, us_symbols)
    quotas = (
        MarketQuota(market=Market.HK, target_count=hk.target_count, weight=0.6),
        MarketQuota(market=Market.US, target_count=us.target_count, weight=0.4),
    )
    return merge_selections(
        as_of=_AS_OF,
        base_currency=Currency.HKD,
        quotas=quotas,
        selections={Market.HK: hk, Market.US: us},
        fx_rate=lambda *_args: 7.8,
    )


def _target(
    hk_symbols: tuple[str, ...],
    us_symbols: tuple[str, ...],
    *,
    method: WeightingMethod = WeightingMethod.EQUAL,
    cash: float = 0.0,
) -> TargetWeightResult:
    return compute_target_weights(
        _merged(hk_symbols, us_symbols),
        TargetWeightConfig(method=method, cash_weight=cash),
    )


def _risk(
    max_position: float = 0.5,
    max_market: float = 1.0,
    min_cash: float = 0.0,
) -> RiskConfig:
    return RiskConfig(
        max_position_pct=max_position,
        max_market_pct=max_market,
        min_cash_pct=min_cash,
    )


class SatisfiedConstraintTests(unittest.TestCase):
    """Verify the no-conflict case."""

    def test_target_satisfying_all_constraints_has_no_conflicts(self) -> None:
        result = apply_concentration_constraints(
            _target(("0001.HK", "0002.HK"), ("AAPL",)),
            _risk(max_position=0.5, max_market=1.0, min_cash=0.0),
        )
        self.assertEqual(result.conflicts, ())
        self.assertEqual(len(result.weights), 3)
        self.assertEqual(result.cash_weight, 0.0)


class SingleStockCapTests(unittest.TestCase):
    """Verify the single-stock cap (单股上限)."""

    def test_overweight_symbol_capped_and_conflict_reported(self) -> None:
        result = apply_concentration_constraints(
            _target(("0001.HK",), ()),
            _risk(max_position=0.3, max_market=1.0, min_cash=0.0),
        )
        self.assertAlmostEqual(result.weight_of(Market.HK, "0001.HK") or 0.0, 0.3)
        self.assertAlmostEqual(result.cash_weight, 0.7)
        self.assertEqual(len(result.conflicts), 1)
        conflict = result.conflicts[0]
        self.assertIs(conflict.constraint, ConstraintKind.MAX_POSITION)
        self.assertEqual(conflict.scope, "HK/0001.HK")

    def test_symbol_at_cap_not_conflicted(self) -> None:
        result = apply_concentration_constraints(
            _target(("0001.HK",), ()),
            _risk(max_position=1.0, max_market=1.0, min_cash=0.0),
        )
        self.assertEqual(result.conflicts, ())
        self.assertAlmostEqual(result.weight_of(Market.HK, "0001.HK") or 0.0, 1.0)


class SingleMarketCapTests(unittest.TestCase):
    """Verify the single-market cap (单市场上限)."""

    def test_overweight_market_scaled_and_conflict_reported(self) -> None:
        result = apply_concentration_constraints(
            _target(("0001.HK", "0002.HK"), ()),
            _risk(max_position=0.5, max_market=0.6, min_cash=0.0),
        )
        self.assertAlmostEqual(result.market_total(Market.HK), 0.6)
        self.assertAlmostEqual(result.weight_of(Market.HK, "0001.HK") or 0.0, 0.3)
        self.assertAlmostEqual(result.cash_weight, 0.4)
        conflict = result.conflicts[0]
        self.assertIs(conflict.constraint, ConstraintKind.MAX_MARKET)
        self.assertEqual(conflict.scope, "HK")


class MinCashTests(unittest.TestCase):
    """Verify the minimum cash ratio (最小现金比例)."""

    def test_cash_raised_to_floor_and_conflict_reported(self) -> None:
        result = apply_concentration_constraints(
            _target(("0001.HK", "0002.HK", "0003.HK", "0004.HK"), ()),
            _risk(max_position=0.5, max_market=1.0, min_cash=0.2),
        )
        self.assertAlmostEqual(result.cash_weight, 0.2)
        self.assertAlmostEqual(result.total_equity_weight, 0.8)
        for weight in result.weights:
            self.assertAlmostEqual(weight.weight, 0.2)
        conflict = result.conflicts[0]
        self.assertIs(conflict.constraint, ConstraintKind.MIN_CASH)
        self.assertEqual(conflict.scope, "portfolio")


class CombinedConstraintTests(unittest.TestCase):
    """Verify multiple constraints interact deterministically."""

    def test_min_cash_and_market_cap_both_applied(self) -> None:
        result = apply_concentration_constraints(
            _target(("0001.HK", "0002.HK", "0003.HK"), ()),
            _risk(max_position=0.5, max_market=0.5, min_cash=0.2),
        )
        # min cash scales equity 1.0 -> 0.8 (each 0.8/3); market total 0.8 > 0.5
        # -> scaled to 0.5 (each 0.5/3); freed 0.3 to cash.
        self.assertAlmostEqual(result.cash_weight, 0.5)
        self.assertAlmostEqual(result.market_total(Market.HK), 0.5)
        kinds = {conflict.constraint for conflict in result.conflicts}
        self.assertIn(ConstraintKind.MIN_CASH, kinds)
        self.assertIn(ConstraintKind.MAX_MARKET, kinds)


class AccessorAndReadableTests(unittest.TestCase):
    """Verify the accessors and readable output."""

    def test_weight_of_returns_none_for_unheld(self) -> None:
        result = apply_concentration_constraints(
            _target(("0001.HK",), ("AAPL",)),
            _risk(),
        )
        self.assertIsNotNone(result.weight_of(Market.HK, "0001.HK"))
        self.assertIsNone(result.weight_of(Market.HK, "0009.HK"))

    def test_readable_includes_conflicts(self) -> None:
        result = apply_concentration_constraints(
            _target(("0001.HK",), ()),
            _risk(max_position=0.3, max_market=1.0, min_cash=0.0),
        )
        summary = result.readable()
        self.assertIn("Constrained portfolio for 2026-03-31 (base HKD):", summary)
        self.assertIn("HK/0001.HK: 0.3000", summary)
        self.assertIn("cash: 0.7000; equity: 0.3000", summary)
        self.assertIn("[max_position] HK/0001.HK:", summary)

    def test_readable_reports_no_conflicts(self) -> None:
        result = apply_concentration_constraints(
            _target(("0001.HK",), ()),
            _risk(max_position=1.0, max_market=1.0, min_cash=0.0),
        )
        self.assertIn("no constraint conflicts", result.readable())


class DeterminismTests(unittest.TestCase):
    """Verify the constraint application is replayable."""

    def test_repeat_application_identical(self) -> None:
        target = _target(("0001.HK", "0002.HK", "0003.HK"), ("AAPL",))
        risk = _risk(max_position=0.4, max_market=0.8, min_cash=0.1)
        first = apply_concentration_constraints(target, risk)
        second = apply_concentration_constraints(target, risk)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
