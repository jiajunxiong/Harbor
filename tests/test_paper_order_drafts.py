"""Paper order draft tests (MVP 4 / SP 4.16).

Verifies that buy/sell order drafts are derived from the target portfolio
versus current positions (direction, quantity, price type) and that cash
shortfalls are surfaced rather than silently executed.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_domain import PaperPriceType, SignalIntention
from harbor.core.paper_order_drafts import (
    PaperOrderDraftError,
    derive_order_drafts,
)
from harbor.core.paper_signal import build_signal_intention
from harbor.core.paper_target_portfolio import derive_target_portfolio

_TRADE = date(2026, 1, 2)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _signal() -> SignalIntention:
    return build_signal_intention(
        intention_id="sig-1",
        paper_run_id="paper-1",
        strategy="mvp3-qualified",
        strategy_version="1.0.0",
        market=Market.HK,
        rebalance_date=_TRADE,
        target_weights=(("0001.HK", 0.5), ("0002.HK", 0.5)),
        source_run_id="mvp3-run-1",
        created_at=_utc_at(2026, 1, 1, 12),
    )


def _prices() -> dict[tuple[Market, str], float]:
    return {(Market.HK, "0001.HK"): 50.0, (Market.HK, "0002.HK"): 20.0}


def _target(positions: dict[tuple[Market, str], float] | None = None) -> object:
    return derive_target_portfolio(
        signal=_signal(),
        positions=positions or {},
        prices=_prices(),
        portfolio_value=1_000_000.0,
        available_cash=1_000_000.0,
        base_currency=Currency.HKD,
        fx_rate=lambda _from, _to, _as_of: None,
    )


def _drafts(**overrides: object):
    """Derive order drafts with overridable kwargs."""
    fields: dict[str, object] = {
        "target": _target(),
        "positions": {},
        "available_cash": 1_000_000.0,
        "price_type": PaperPriceType.REFERENCE,
    }
    fields.update(overrides)
    return derive_order_drafts(**fields)  # type: ignore[arg-type]


class DeriveOrderDraftsTests(unittest.TestCase):
    """The order draft derivation (SP 4.16)."""

    def test_buy_drafts_from_flat(self) -> None:
        result = _drafts()
        by_symbol = {draft.symbol: draft for draft in result.drafts}
        self.assertEqual(len(result.drafts), 2)
        self.assertEqual(by_symbol["0001.HK"].side, OrderSide.BUY)
        self.assertAlmostEqual(by_symbol["0001.HK"].quantity, 10_000.0, places=6)
        self.assertEqual(by_symbol["0001.HK"].price_type, PaperPriceType.REFERENCE)

    def test_sell_draft_when_above_target(self) -> None:
        target = _target(positions={(Market.HK, "0001.HK"): 20_000.0})
        result = derive_order_drafts(
            target=target,
            positions={(Market.HK, "0001.HK"): 20_000.0},
            available_cash=1_000_000.0,
        )
        drafts = {draft.symbol: draft for draft in result.drafts}
        self.assertEqual(drafts["0001.HK"].side, OrderSide.SELL)
        self.assertAlmostEqual(drafts["0001.HK"].quantity, 10_000.0, places=6)

    def test_skip_when_at_target(self) -> None:
        target = _target(
            positions={(Market.HK, "0001.HK"): 10_000.0, (Market.HK, "0002.HK"): 25_000.0}
        )
        result = derive_order_drafts(
            target=target,
            positions={(Market.HK, "0001.HK"): 10_000.0, (Market.HK, "0002.HK"): 25_000.0},
            available_cash=1_000_000.0,
        )
        self.assertEqual(result.drafts, ())
        self.assertEqual(len(result.skipped), 2)

    def test_cash_shortfall_surfaced(self) -> None:
        result = _drafts(available_cash=100_000.0)
        self.assertAlmostEqual(result.buy_value_base, 1_000_000.0, places=6)
        self.assertAlmostEqual(result.cash_shortfall, 900_000.0, places=6)

    def test_negative_cash_rejected(self) -> None:
        with self.assertRaises(PaperOrderDraftError):
            _drafts(available_cash=-1.0)

    def test_readable(self) -> None:
        result = _drafts()
        self.assertIn("order drafts for", result.readable())
        self.assertIn("0001.HK", result.readable())
