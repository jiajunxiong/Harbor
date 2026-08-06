"""Order draft tests (MVP 2 / SP 2.36).

Verifies buy/sell order drafts from current positions and target weights, FX
conversion for cross-market symbols, refusal of missing FX / prices, cash
feasibility and determinism.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import MarketQuota, RiskConfig
from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.concentration import ConstrainedPortfolio, apply_concentration_constraints
from harbor.core.cross_market_merge import MergedSelection, merge_selections
from harbor.core.fx import FxConversionError
from harbor.core.market_selector import SelectionResult, select_candidates
from harbor.core.order_drafts import generate_order_drafts
from harbor.core.target_weight import (
    TargetWeightConfig,
    WeightingMethod,
    compute_target_weights,
)

_AS_OF = date(2026, 3, 31)
_USD_TO_HKD = 7.8


def _selection(market: Market, symbols: tuple[str, ...]) -> SelectionResult:
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
        fx_rate=lambda *_args: _USD_TO_HKD,
    )


def _portfolio(
    hk_symbols: tuple[str, ...],
    us_symbols: tuple[str, ...],
    *,
    method: WeightingMethod = WeightingMethod.EQUAL,
    cash: float = 0.0,
) -> ConstrainedPortfolio:
    target = compute_target_weights(
        _merged(hk_symbols, us_symbols),
        TargetWeightConfig(method=method, cash_weight=cash),
    )
    risk = RiskConfig(max_position_pct=1.0, max_market_pct=1.0, min_cash_pct=0.0)
    return apply_concentration_constraints(target, risk)


def _fx(rate: float | None = _USD_TO_HKD) -> object:
    def accessor(from_currency: Currency, to_currency: Currency, _: date) -> float | None:
        if (from_currency, to_currency) == (Currency.USD, Currency.HKD):
            return rate
        return None

    return accessor


class DraftDirectionTests(unittest.TestCase):
    """Verify BUY/SELL direction from the position delta."""

    def test_buy_and_sell_drafts(self) -> None:
        portfolio = _portfolio(("0001.HK", "0002.HK"), ())
        result = generate_order_drafts(
            portfolio,
            positions={(Market.HK, "0001.HK"): 10.0, (Market.HK, "0002.HK"): 70.0},
            prices={(Market.HK, "0001.HK"): 10.0, (Market.HK, "0002.HK"): 10.0},
            portfolio_value=1_000.0,
            available_cash=500.0,
            fx_rate=_fx(),  # type: ignore[arg-type]
        )
        by_symbol = {draft.symbol: draft for draft in result.drafts}
        self.assertIs(by_symbol["0001.HK"].side, OrderSide.BUY)
        self.assertAlmostEqual(by_symbol["0001.HK"].quantity, 40.0)
        self.assertIs(by_symbol["0002.HK"].side, OrderSide.SELL)
        self.assertAlmostEqual(by_symbol["0002.HK"].quantity, 20.0)
        self.assertAlmostEqual(result.buy_value_base, 400.0)
        self.assertAlmostEqual(result.sell_value_base, 200.0)
        self.assertAlmostEqual(result.cash_shortfall, 0.0)

    def test_at_target_is_skipped(self) -> None:
        portfolio = _portfolio(("0001.HK", "0002.HK"), ())
        result = generate_order_drafts(
            portfolio,
            positions={(Market.HK, "0001.HK"): 50.0, (Market.HK, "0002.HK"): 50.0},
            prices={(Market.HK, "0001.HK"): 10.0, (Market.HK, "0002.HK"): 10.0},
            portfolio_value=1_000.0,
            available_cash=0.0,
            fx_rate=_fx(),  # type: ignore[arg-type]
        )
        self.assertEqual(result.drafts, ())
        self.assertEqual(len(result.skipped), 2)

    def test_drafts_sorted_by_market_then_symbol(self) -> None:
        portfolio = _portfolio(("0002.HK", "0001.HK"), ())
        result = generate_order_drafts(
            portfolio,
            positions={},
            prices={
                (Market.HK, "0001.HK"): 10.0,
                (Market.HK, "0002.HK"): 10.0,
            },
            portfolio_value=1_000.0,
            available_cash=1_000.0,
            fx_rate=_fx(),  # type: ignore[arg-type]
        )
        self.assertEqual([draft.symbol for draft in result.drafts], ["0001.HK", "0002.HK"])


class CrossMarketFxCases(unittest.TestCase):
    """Verify FX conversion for cross-market symbols (SP 2.12)."""

    def test_us_symbol_priced_in_base_via_fx(self) -> None:
        portfolio = _portfolio((), ("AAPL",))
        result = generate_order_drafts(
            portfolio,
            positions={},
            prices={(Market.US, "AAPL"): 10.0},
            portfolio_value=1_000.0,
            available_cash=1_000.0,
            fx_rate=_fx(),  # type: ignore[arg-type]
        )
        draft = result.drafts[0]
        self.assertIs(draft.side, OrderSide.BUY)
        self.assertAlmostEqual(draft.quantity, 1_000.0 / _USD_TO_HKD / 10.0)
        self.assertAlmostEqual(result.buy_value_base, 1_000.0)

    def test_missing_fx_is_refused(self) -> None:
        portfolio = _portfolio((), ("AAPL",))
        with self.assertRaisesRegex(FxConversionError, "USD->HKD"):
            generate_order_drafts(
                portfolio,
                positions={},
                prices={(Market.US, "AAPL"): 10.0},
                portfolio_value=1_000.0,
                available_cash=1_000.0,
                fx_rate=_fx(None),  # type: ignore[arg-type]
            )


class ValidationTests(unittest.TestCase):
    """Verify price and value validation."""

    def test_missing_price_is_refused(self) -> None:
        portfolio = _portfolio(("0001.HK",), ())
        with self.assertRaisesRegex(ValueError, "price"):
            generate_order_drafts(
                portfolio,
                positions={},
                prices={},
                portfolio_value=1_000.0,
                available_cash=1_000.0,
                fx_rate=_fx(),  # type: ignore[arg-type]
            )

    def test_non_positive_price_is_refused(self) -> None:
        portfolio = _portfolio(("0001.HK",), ())
        with self.assertRaisesRegex(ValueError, "price"):
            generate_order_drafts(
                portfolio,
                positions={},
                prices={(Market.HK, "0001.HK"): 0.0},
                portfolio_value=1_000.0,
                available_cash=1_000.0,
                fx_rate=_fx(),  # type: ignore[arg-type]
            )

    def test_rejects_non_positive_portfolio_value(self) -> None:
        portfolio = _portfolio(("0001.HK",), ())
        with self.assertRaisesRegex(ValueError, "portfolio_value"):
            generate_order_drafts(
                portfolio,
                positions={},
                prices={(Market.HK, "0001.HK"): 10.0},
                portfolio_value=0.0,
                available_cash=1_000.0,
                fx_rate=_fx(),  # type: ignore[arg-type]
            )


class CashFeasibilityTests(unittest.TestCase):
    """Verify the cash shortfall surfacing (可用现金)."""

    def test_shortfall_when_cash_insufficient(self) -> None:
        portfolio = _portfolio(("0001.HK",), ())
        result = generate_order_drafts(
            portfolio,
            positions={},
            prices={(Market.HK, "0001.HK"): 10.0},
            portfolio_value=1_000.0,
            available_cash=300.0,
            fx_rate=_fx(),  # type: ignore[arg-type]
        )
        self.assertAlmostEqual(result.cash_shortfall, 700.0)

    def test_no_shortfall_when_cash_covers_buys(self) -> None:
        portfolio = _portfolio(("0001.HK",), ())
        result = generate_order_drafts(
            portfolio,
            positions={},
            prices={(Market.HK, "0001.HK"): 10.0},
            portfolio_value=1_000.0,
            available_cash=1_000.0,
            fx_rate=_fx(),  # type: ignore[arg-type]
        )
        self.assertAlmostEqual(result.cash_shortfall, 0.0)


class ReadableAndDeterminismTests(unittest.TestCase):
    """Verify the readable summary and replayability."""

    def test_readable_summary(self) -> None:
        portfolio = _portfolio(("0001.HK", "0002.HK"), ())
        result = generate_order_drafts(
            portfolio,
            positions={(Market.HK, "0001.HK"): 10.0, (Market.HK, "0002.HK"): 70.0},
            prices={(Market.HK, "0001.HK"): 10.0, (Market.HK, "0002.HK"): 10.0},
            portfolio_value=1_000.0,
            available_cash=100.0,
            fx_rate=_fx(),  # type: ignore[arg-type]
        )
        summary = result.readable()
        self.assertIn("Order drafts for 2026-03-31 (base HKD, value 1000.00):", summary)
        self.assertIn("BUY HK/0001.HK: 40.0000 HKD", summary)
        self.assertIn("SELL HK/0002.HK: 20.0000 HKD", summary)
        self.assertIn("buy value 400.00; sell value 200.00", summary)
        self.assertIn("cash shortfall: 100.00", summary)

    def test_repeat_generation_identical(self) -> None:
        portfolio = _portfolio(("0001.HK", "0002.HK"), ("AAPL",))
        kwargs = {
            "positions": {(Market.HK, "0001.HK"): 10.0},
            "prices": {
                (Market.HK, "0001.HK"): 10.0,
                (Market.HK, "0002.HK"): 10.0,
                (Market.US, "AAPL"): 10.0,
            },
            "portfolio_value": 1_000.0,
            "available_cash": 1_000.0,
            "fx_rate": _fx(),  # type: ignore[arg-type]
        }
        first = generate_order_drafts(portfolio, **kwargs)
        second = generate_order_drafts(portfolio, **kwargs)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
