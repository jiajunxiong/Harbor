"""Cross-market merge tests (MVP 2 / SP 2.27).

Verifies that standardized HK (SP 2.25) and US (SP 2.26) selections are merged
by base currency and market quotas, that a cross-market combination is
explicitly refused when the required FX rate is missing or non-positive
(SP 2.12 / SP 2.27), and that the merged output preserves each market's
snapshot with per-symbol quote currency, score and rank.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import MarketQuota
from harbor.core.backtest_domain import Currency, Market
from harbor.core.cross_market_merge import CrossMarketFxError, merge_selections
from harbor.core.market_selector import SelectionResult, select_candidates

_AS_OF = date(2026, 3, 31)

_HK_SCORES = {"0005.HK": 0.9, "0700.HK": 0.8, "0001.HK": 0.7}
_US_SCORES = {"AAPL": 0.95, "MSFT": 0.85, "GOOGL": 0.75}

_HK_QUOTA = MarketQuota(market=Market.HK, target_count=2, weight=0.6)
_US_QUOTA = MarketQuota(market=Market.US, target_count=2, weight=0.4)
_USD_TO_HKD = 7.85


def _build_selections() -> tuple[SelectionResult, SelectionResult]:
    """Return HK and US selections built through the SP 2.25/2.26 selectors."""
    hk = select_candidates(Market.HK, _AS_OF, _HK_SCORES, target_count=2)
    us = select_candidates(Market.US, _AS_OF, _US_SCORES, target_count=2)
    return hk, us


def _always_rate(from_currency: Currency, to_currency: Currency, _: date) -> float | None:
    """FX accessor returning a fixed USD->HKD rate."""
    return _USD_TO_HKD if (from_currency, to_currency) == (Currency.USD, Currency.HKD) else None


class MergeValidationTests(unittest.TestCase):
    """Verify merge input validation (SP 2.27)."""

    def test_refuses_empty_quotas(self) -> None:
        with self.assertRaisesRegex(ValueError, "quota"):
            merge_selections(
                as_of=_AS_OF,
                base_currency=Currency.HKD,
                quotas=[],
                selections={},
                fx_rate=_always_rate,
            )

    def test_refuses_duplicate_quota_markets(self) -> None:
        hk, _ = _build_selections()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            merge_selections(
                as_of=_AS_OF,
                base_currency=Currency.HKD,
                quotas=[_HK_QUOTA, _HK_QUOTA],
                selections={Market.HK: hk},
                fx_rate=_always_rate,
            )

    def test_refuses_missing_selection_for_quota_market(self) -> None:
        _, us = _build_selections()
        with self.assertRaisesRegex(ValueError, "Missing selection"):
            merge_selections(
                as_of=_AS_OF,
                base_currency=Currency.HKD,
                quotas=[_HK_QUOTA, _US_QUOTA],
                selections={Market.US: us},
                fx_rate=_always_rate,
            )

    def test_refuses_target_count_mismatch(self) -> None:
        hk, us = _build_selections()
        quota = MarketQuota(market=Market.US, target_count=1, weight=0.4)
        with self.assertRaisesRegex(ValueError, "target"):
            merge_selections(
                as_of=_AS_OF,
                base_currency=Currency.HKD,
                quotas=[_HK_QUOTA, quota],
                selections={Market.HK: hk, Market.US: us},
                fx_rate=_always_rate,
            )

    def test_refuses_as_of_mismatch(self) -> None:
        hk, _ = _build_selections()
        with self.assertRaisesRegex(ValueError, "as-of"):
            merge_selections(
                as_of=date(2026, 1, 30),
                base_currency=Currency.HKD,
                quotas=[_HK_QUOTA],
                selections={Market.HK: hk},
                fx_rate=_always_rate,
            )


class MergeFxTests(unittest.TestCase):
    """Verify base-currency and FX behavior (SP 2.27)."""

    def test_merges_hk_and_us_into_base_currency(self) -> None:
        hk, us = _build_selections()
        merged = merge_selections(
            as_of=_AS_OF,
            base_currency=Currency.HKD,
            quotas=[_HK_QUOTA, _US_QUOTA],
            selections={Market.HK: hk, Market.US: us},
            fx_rate=_always_rate,
        )
        self.assertEqual(merged.selected, ("0005.HK", "0700.HK", "AAPL", "MSFT"))
        self.assertTrue(merged.fx_required)
        # HK needs no FX (same currency); US uses the provided rate.
        self.assertEqual(merged.fx_rates[Market.HK], 1.0)
        self.assertEqual(merged.fx_rates[Market.US], _USD_TO_HKD)

    def test_no_fx_needed_when_base_matches_quote_currency(self) -> None:
        hk, _ = _build_selections()
        merged = merge_selections(
            as_of=_AS_OF,
            base_currency=Currency.HKD,
            quotas=[_HK_QUOTA],
            selections={Market.HK: hk},
            fx_rate=lambda *_args: None,
        )
        self.assertFalse(merged.fx_required)
        self.assertEqual(merged.fx_rates[Market.HK], 1.0)

    def test_refuses_cross_market_when_fx_missing(self) -> None:
        hk, us = _build_selections()
        with self.assertRaisesRegex(CrossMarketFxError, "USD->HKD"):
            merge_selections(
                as_of=_AS_OF,
                base_currency=Currency.HKD,
                quotas=[_HK_QUOTA, _US_QUOTA],
                selections={Market.HK: hk, Market.US: us},
                fx_rate=lambda *_args: None,
            )

    def test_refuses_cross_market_when_fx_non_positive(self) -> None:
        hk, us = _build_selections()
        with self.assertRaisesRegex(CrossMarketFxError, "USD->HKD"):
            merge_selections(
                as_of=_AS_OF,
                base_currency=Currency.HKD,
                quotas=[_HK_QUOTA, _US_QUOTA],
                selections={Market.HK: hk, Market.US: us},
                fx_rate=lambda *_args: 0.0,
            )

    def test_refuses_fx_for_single_market_when_needed(self) -> None:
        _, us = _build_selections()
        with self.assertRaisesRegex(CrossMarketFxError, "USD->HKD"):
            merge_selections(
                as_of=_AS_OF,
                base_currency=Currency.HKD,
                quotas=[_US_QUOTA],
                selections={Market.US: us},
                fx_rate=lambda *_args: None,
            )


class MergeOutputTests(unittest.TestCase):
    """Verify the merged selection structure (SP 2.27)."""

    def test_preserves_per_market_selection_snapshot(self) -> None:
        hk, us = _build_selections()
        merged = merge_selections(
            as_of=_AS_OF,
            base_currency=Currency.HKD,
            quotas=[_HK_QUOTA, _US_QUOTA],
            selections={Market.HK: hk, Market.US: us},
            fx_rate=_always_rate,
        )
        self.assertEqual(merged.selections, (hk, us))
        self.assertEqual(merged.quotas, (_HK_QUOTA, _US_QUOTA))
        self.assertEqual(merged.as_of, _AS_OF)
        self.assertEqual(merged.base_currency, Currency.HKD)

    def test_symbols_flattened_with_quote_currency_score_rank(self) -> None:
        hk, us = _build_selections()
        merged = merge_selections(
            as_of=_AS_OF,
            base_currency=Currency.HKD,
            quotas=[_HK_QUOTA, _US_QUOTA],
            selections={Market.HK: hk, Market.US: us},
            fx_rate=_always_rate,
        )
        symbols = merged.symbols
        self.assertEqual(
            [s.symbol for s in symbols],
            ["0005.HK", "0700.HK", "0001.HK", "AAPL", "MSFT", "GOOGL"],
        )
        self.assertEqual(
            [s.market for s in symbols],
            [Market.HK, Market.HK, Market.HK, Market.US, Market.US, Market.US],
        )
        self.assertEqual(
            [s.quote_currency for s in symbols],
            [Currency.HKD, Currency.HKD, Currency.HKD, Currency.USD, Currency.USD, Currency.USD],
        )
        self.assertEqual([s.rank for s in symbols], [1, 2, 3, 1, 2, 3])
        self.assertEqual([s.selected for s in symbols], [True, True, False, True, True, False])
        self.assertEqual(symbols[0].score, 0.9)
        self.assertEqual(merged.selected, ("0005.HK", "0700.HK", "AAPL", "MSFT"))

    def test_all_ranked_symbols_included_with_selection_flags(self) -> None:
        hk = select_candidates(Market.HK, _AS_OF, _HK_SCORES, target_count=1)
        quota = MarketQuota(market=Market.HK, target_count=1, weight=1.0)
        merged = merge_selections(
            as_of=_AS_OF,
            base_currency=Currency.HKD,
            quotas=[quota],
            selections={Market.HK: hk},
            fx_rate=lambda *_args: None,
        )
        self.assertEqual([s.symbol for s in merged.symbols], ["0005.HK", "0700.HK", "0001.HK"])
        self.assertEqual([s.selected for s in merged.symbols], [True, False, False])
        self.assertEqual(merged.selected, ("0005.HK",))

    def test_selected_ordered_by_quota(self) -> None:
        us = select_candidates(Market.US, _AS_OF, _US_SCORES, target_count=2)
        hk = select_candidates(Market.HK, _AS_OF, _HK_SCORES, target_count=2)
        merged = merge_selections(
            as_of=_AS_OF,
            base_currency=Currency.HKD,
            quotas=[_US_QUOTA, _HK_QUOTA],
            selections={Market.HK: hk, Market.US: us},
            fx_rate=_always_rate,
        )
        self.assertEqual(merged.selected, ("AAPL", "MSFT", "0005.HK", "0700.HK"))

    def test_readable_summary(self) -> None:
        hk, us = _build_selections()
        merged = merge_selections(
            as_of=_AS_OF,
            base_currency=Currency.HKD,
            quotas=[_HK_QUOTA, _US_QUOTA],
            selections={Market.HK: hk, Market.US: us},
            fx_rate=_always_rate,
        )
        summary = merged.readable()
        self.assertIn("Merged selection for 2026-03-31, base HKD:", summary)
        self.assertIn("HK (weight 0.6000, target 2):", summary)
        self.assertIn("FX to base: none needed", summary)
        self.assertIn("FX to base: USD->HKD 7.8500", summary)
        self.assertIn("selected (4): 0005.HK, 0700.HK, AAPL, MSFT", summary)


class MergeDeterminismTests(unittest.TestCase):
    """Verify the merge is replayable (SP 2.27)."""

    def test_repeat_calls_identical(self) -> None:
        hk, us = _build_selections()
        first = merge_selections(
            as_of=_AS_OF,
            base_currency=Currency.HKD,
            quotas=[_HK_QUOTA, _US_QUOTA],
            selections={Market.HK: hk, Market.US: us},
            fx_rate=_always_rate,
        )
        second = merge_selections(
            as_of=_AS_OF,
            base_currency=Currency.HKD,
            quotas=[_HK_QUOTA, _US_QUOTA],
            selections={Market.HK: hk, Market.US: us},
            fx_rate=_always_rate,
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
