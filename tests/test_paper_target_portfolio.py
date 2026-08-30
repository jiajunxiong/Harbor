"""Paper target portfolio tests (MVP 4 / SP 4.15).

Verifies that the target portfolio is derived from the signal, current
positions, available cash and FX prices with a preserved calculation basis,
and that missing prices or FX are refused rather than assumed.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market
from harbor.core.fx import FxConversionError
from harbor.core.paper_domain import SignalIntention
from harbor.core.paper_signal import build_signal_intention
from harbor.core.paper_target_portfolio import (
    PaperTargetPortfolioError,
    TargetPositionBasis,
    derive_target_portfolio,
)

_TRADE = date(2026, 1, 2)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _signal(
    weights: tuple[tuple[str, float], ...] = (("0001.HK", 0.5), ("0002.HK", 0.5)),
    market: Market = Market.HK,
) -> SignalIntention:
    return build_signal_intention(
        intention_id="sig-1",
        paper_run_id="paper-1",
        strategy="mvp3-qualified",
        strategy_version="1.0.0",
        market=market,
        rebalance_date=_TRADE,
        target_weights=weights,
        source_run_id="mvp3-run-1",
        created_at=_utc_at(2026, 1, 1, 12),
    )


def _fx(rate: float | None):
    """Return a fixed FX accessor (quote -> base)."""
    return lambda _from, _to, _as_of: rate


def _hk_prices() -> dict[tuple[Market, str], float]:
    return {(Market.HK, "0001.HK"): 50.0, (Market.HK, "0002.HK"): 20.0}


def _derive(**overrides: object):
    """Derive a target portfolio with overridable kwargs."""
    fields: dict[str, object] = {
        "signal": _signal(),
        "positions": {},
        "prices": _hk_prices(),
        "portfolio_value": 1_000_000.0,
        "available_cash": 1_000_000.0,
        "base_currency": Currency.HKD,
        "fx_rate": _fx(None),
    }
    fields.update(overrides)
    return derive_target_portfolio(**fields)  # type: ignore[arg-type]


class DeriveTargetPortfolioTests(unittest.TestCase):
    """The target portfolio derivation (SP 4.15)."""

    def test_same_currency_targets(self) -> None:
        target = _derive()
        self.assertEqual(target.as_of, _TRADE)
        self.assertEqual(len(target.positions), 2)
        by_symbol = {position.symbol: position for position in target.positions}
        # weight 0.5 * 1M = 500k base; /50 = 10,000 shares
        self.assertAlmostEqual(by_symbol["0001.HK"].quantity, 10_000.0, places=6)
        self.assertAlmostEqual(by_symbol["0002.HK"].quantity, 25_000.0, places=6)
        self.assertEqual(target.cash_shortfall, 0.0)

    def test_basis_preserved(self) -> None:
        target = _derive()
        basis = {entry.symbol: entry for entry in target.basis}
        entry = basis["0001.HK"]
        self.assertIsInstance(entry, TargetPositionBasis)
        self.assertEqual(entry.weight, 0.5)
        self.assertEqual(entry.price, 50.0)
        self.assertEqual(entry.fx_rate, 1.0)
        self.assertAlmostEqual(entry.target_value_base, 500_000.0, places=6)
        self.assertAlmostEqual(entry.target_value_quote, 500_000.0, places=6)
        self.assertIn("0001.HK", entry.readable())

    def test_current_positions_reduce_delta(self) -> None:
        target = _derive(positions={(Market.HK, "0001.HK"): 10_000.0})
        # 0001.HK already at target; 0002.HK still needs 25,000 shares -> 500k buy
        self.assertAlmostEqual(target.buy_value_base, 500_000.0, places=6)

    def test_sell_delta(self) -> None:
        target = _derive(positions={(Market.HK, "0001.HK"): 20_000.0})
        # 0001.HK target 10,000 -> sell 10,000 @ 50 = 500k; 0002.HK buy 25,000 @20 = 500k
        self.assertAlmostEqual(target.sell_value_base, 500_000.0, places=6)
        self.assertAlmostEqual(target.buy_value_base, 500_000.0, places=6)
        self.assertEqual(target.cash_shortfall, 0.0)

    def test_cash_shortfall_surfaced(self) -> None:
        target = _derive(available_cash=100_000.0)
        # buys 500k + 500k = 1M, cash + sells = 100k -> shortfall 900k
        self.assertAlmostEqual(target.cash_shortfall, 900_000.0, places=6)

    def test_missing_price_rejected(self) -> None:
        prices = {(Market.HK, "0001.HK"): 50.0}  # no 0002.HK
        with self.assertRaises(PaperTargetPortfolioError):
            _derive(prices=prices)

    def test_cross_currency_missing_fx_refused(self) -> None:
        signal = _signal(
            weights=(("AAPL", 1.0),),
            market=Market.US,
        )
        with self.assertRaises(FxConversionError) as ctx:
            _derive(
                signal=signal,
                prices={(Market.US, "AAPL"): 100.0},
                base_currency=Currency.HKD,
                fx_rate=_fx(None),
            )
        self.assertIn("refusing to assume 1:1", str(ctx.exception))

    def test_cross_currency_with_fx(self) -> None:
        signal = _signal(weights=(("AAPL", 1.0),), market=Market.US)
        target = _derive(
            signal=signal,
            prices={(Market.US, "AAPL"): 100.0},
            base_currency=Currency.HKD,
            fx_rate=_fx(7.8),
        )
        # 1M HKD / 7.8 = 128205.13 USD / 100 = 1282.05 shares
        self.assertAlmostEqual(target.positions[0].quantity, 1_000_000.0 / 7.8 / 100.0, places=6)
        entry = target.basis[0]
        self.assertEqual(entry.fx_rate, 7.8)

    def test_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(PaperTargetPortfolioError):
            _derive(portfolio_value=0.0)
        with self.assertRaises(PaperTargetPortfolioError):
            _derive(available_cash=-1.0)

    def test_readable_and_weight_of(self) -> None:
        target = _derive()
        self.assertEqual(target.weight_of(Market.HK, "0001.HK"), 0.5)
        self.assertIsNone(target.weight_of(Market.HK, "9999.HK"))
        self.assertIn("target portfolio", target.readable())
