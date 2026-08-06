"""Target weight model tests (MVP 2 / SP 2.34).

Verifies equal-weight and market-quota weighting, the cash weight, the
deterministic largest-remainder rounding rule, and replayability.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import MarketQuota
from harbor.core.backtest_domain import Currency, Market
from harbor.core.cross_market_merge import MergedSelection, merge_selections
from harbor.core.market_selector import SelectionResult, select_candidates
from harbor.core.target_weight import (
    TargetWeightConfig,
    WeightingMethod,
    compute_target_weights,
)

_AS_OF = date(2026, 3, 31)


def _selection(market: Market, symbols: tuple[str, ...]) -> SelectionResult:
    """Select every symbol (descending scores so rank order is preserved).

    An empty symbol set yields an empty selection with a positive target count.
    """
    if not symbols:
        return select_candidates(market, _AS_OF, {}, target_count=3)
    scores = {symbol: 1.0 - index * 0.01 for index, symbol in enumerate(symbols)}
    return select_candidates(market, _AS_OF, scores, target_count=len(symbols))


def _merged(
    hk_symbols: tuple[str, ...],
    us_symbols: tuple[str, ...],
    hk_weight: float = 0.6,
    us_weight: float = 0.4,
) -> MergedSelection:
    hk_selection = _selection(Market.HK, hk_symbols)
    us_selection = _selection(Market.US, us_symbols)
    quotas = (
        MarketQuota(
            market=Market.HK,
            target_count=hk_selection.target_count,
            weight=hk_weight,
        ),
        MarketQuota(
            market=Market.US,
            target_count=us_selection.target_count,
            weight=us_weight,
        ),
    )
    selections = {
        Market.HK: hk_selection,
        Market.US: us_selection,
    }
    return merge_selections(
        as_of=_AS_OF,
        base_currency=Currency.HKD,
        quotas=quotas,
        selections=selections,
        fx_rate=lambda *_args: 7.8,
    )


class EqualWeightTests(unittest.TestCase):
    """Verify the equal-weight method (等权)."""

    def test_equal_weight_across_all_selected(self) -> None:
        result = compute_target_weights(_merged(("0001.HK", "0002.HK"), ("AAPL",)))
        self.assertEqual(len(result.weights), 3)
        for weight in result.weights:
            self.assertAlmostEqual(weight.weight, 1.0 / 3, places=3)
        self.assertAlmostEqual(sum(w.weight for w in result.weights), 1.0)

    def test_equal_weight_with_cash(self) -> None:
        result = compute_target_weights(
            _merged(("0001.HK", "0002.HK"), ("AAPL",)),
            TargetWeightConfig(cash_weight=0.2),
        )
        self.assertAlmostEqual(result.cash_weight, 0.2)
        self.assertAlmostEqual(result.total_equity_weight, 0.8)
        for weight in result.weights:
            self.assertAlmostEqual(weight.weight, 0.8 / 3, places=3)


class MarketQuotaWeightTests(unittest.TestCase):
    """Verify the market-quota method (配置化市场配额)."""

    def test_market_quota_split_within_market(self) -> None:
        result = compute_target_weights(
            _merged(("0001.HK", "0002.HK", "0003.HK"), ("AAPL",)),
            TargetWeightConfig(method=WeightingMethod.MARKET_QUOTA),
        )
        self.assertAlmostEqual(result.weight_of(Market.HK, "0001.HK") or 0.0, 0.6 / 3)
        self.assertAlmostEqual(result.weight_of(Market.HK, "0003.HK") or 0.0, 0.6 / 3)
        self.assertAlmostEqual(result.weight_of(Market.US, "AAPL") or 0.0, 0.4)
        self.assertAlmostEqual(sum(w.weight for w in result.weights), 1.0)

    def test_market_quota_with_cash(self) -> None:
        result = compute_target_weights(
            _merged(("0001.HK", "0002.HK"), ("AAPL",)),
            TargetWeightConfig(method=WeightingMethod.MARKET_QUOTA, cash_weight=0.2),
        )
        self.assertAlmostEqual(result.weight_of(Market.HK, "0001.HK") or 0.0, 0.6 * 0.8 / 2)
        self.assertAlmostEqual(result.weight_of(Market.US, "AAPL") or 0.0, 0.4 * 0.8)
        self.assertAlmostEqual(result.total_equity_weight, 0.8)

    def test_equal_and_market_quota_differ(self) -> None:
        merged = _merged(("0001.HK", "0002.HK"), ("AAPL",))
        equal = compute_target_weights(merged, TargetWeightConfig(method=WeightingMethod.EQUAL))
        quota = compute_target_weights(
            merged, TargetWeightConfig(method=WeightingMethod.MARKET_QUOTA)
        )
        self.assertNotEqual(equal.weights, quota.weights)


class RoundingTests(unittest.TestCase):
    """Verify the deterministic largest-remainder rounding (舍入规则)."""

    def test_rounded_weights_sum_to_target(self) -> None:
        result = compute_target_weights(_merged(("0001.HK", "0002.HK"), ("AAPL",)))
        total = sum(weight.weight for weight in result.weights)
        self.assertAlmostEqual(total, round(1.0, result.decimal_places))

    def test_largest_remainder_grants_extra_unit_deterministically(self) -> None:
        # 3 equal symbols: floors to 0.3333 each, one gets the residual unit.
        result = compute_target_weights(_merged(("0001.HK", "0002.HK", "0003.HK"), ()))
        weights = {weight.symbol: weight.weight for weight in result.weights}
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        # Tie broken by symbol ascending, so 0001.HK receives the extra unit.
        self.assertAlmostEqual(weights["0001.HK"], 0.3334)
        self.assertAlmostEqual(weights["0002.HK"], 0.3333)
        self.assertAlmostEqual(weights["0003.HK"], 0.3333)

    def test_zero_decimal_places(self) -> None:
        result = compute_target_weights(
            _merged(("0001.HK", "0002.HK"), ("AAPL",)),
            TargetWeightConfig(decimal_places=0),
        )
        for weight in result.weights:
            self.assertEqual(weight.weight, round(weight.weight))


class EmptyAndAccessorTests(unittest.TestCase):
    """Verify empty selections and the weight accessor."""

    def test_empty_selection_yields_no_weights(self) -> None:
        result = compute_target_weights(_merged((), ()))
        self.assertEqual(result.weights, ())
        self.assertAlmostEqual(result.total_equity_weight, 1.0)

    def test_weight_of_returns_none_for_unselected(self) -> None:
        result = compute_target_weights(_merged(("0001.HK",), ("AAPL",)))
        self.assertIsNotNone(result.weight_of(Market.HK, "0001.HK"))
        self.assertIsNone(result.weight_of(Market.HK, "0009.HK"))


class ValidationTests(unittest.TestCase):
    """Verify the target weight config validation."""

    def test_rejects_out_of_range_cash_weight(self) -> None:
        with self.assertRaisesRegex(ValueError, "cash_weight"):
            TargetWeightConfig(cash_weight=1.0)
        with self.assertRaisesRegex(ValueError, "cash_weight"):
            TargetWeightConfig(cash_weight=-0.1)

    def test_rejects_negative_decimal_places(self) -> None:
        with self.assertRaisesRegex(ValueError, "decimal_places"):
            TargetWeightConfig(decimal_places=-1)


class ReadableAndDeterminismTests(unittest.TestCase):
    """Verify the readable summary and replayability."""

    def test_readable_summary(self) -> None:
        result = compute_target_weights(
            _merged(("0001.HK",), ("AAPL",)),
            TargetWeightConfig(cash_weight=0.2),
        )
        summary = result.readable()
        self.assertIn("Target weights for 2026-03-31 (base HKD, equal):", summary)
        self.assertIn("HK/0001.HK:", summary)
        self.assertIn("US/AAPL:", summary)
        self.assertIn("cash: 0.2000; equity: 0.8000", summary)

    def test_repeat_computation_identical(self) -> None:
        merged = _merged(("0001.HK", "0002.HK", "0003.HK"), ("AAPL",))
        config = TargetWeightConfig(method=WeightingMethod.MARKET_QUOTA, cash_weight=0.1)
        first = compute_target_weights(merged, config)
        second = compute_target_weights(merged, config)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
