"""Paper signal-order consistency suite (MVP 4 / SP 4.27).

Integration-style tests over SP 4.14-4.26 covering the acceptance dimensions:
信号变更 (a signal change re-derives the targets/drafts), 目标差 (target vs
current deltas), 现金不足 (cash shortfalls are surfaced) and 多市场并发订单
(HK/US concurrent orders stay in independent batches, never merged).
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market
from harbor.core.paper_domain import PaperOrder
from harbor.core.paper_multi_market import group_orders_by_market
from harbor.core.paper_order_drafts import derive_order_drafts
from harbor.core.paper_signal import build_signal_intention
from harbor.core.paper_target_portfolio import derive_target_portfolio

_TRADE = date(2026, 1, 2)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _signal(
    weights: tuple[tuple[str, float], ...],
    market: Market,
    intention_id: str,
) -> object:
    return build_signal_intention(
        intention_id=intention_id,
        paper_run_id="paper-1",
        strategy="mvp3-qualified",
        strategy_version="1.0.0",
        market=market,
        rebalance_date=_TRADE,
        target_weights=weights,
        source_run_id="mvp3-run-1",
        created_at=_utc_at(2026, 1, 1, 12),
    )


class SignalChangeTests(unittest.TestCase):
    """A signal change re-derives the targets and drafts (SP 4.27)."""

    def _derive(self, weights: tuple[tuple[str, float], ...]) -> object:
        signal = _signal(weights, Market.HK, "sig-1")
        target = derive_target_portfolio(
            signal=signal,
            positions={},
            prices={(Market.HK, "0001.HK"): 50.0, (Market.HK, "0002.HK"): 20.0},
            portfolio_value=1_000_000.0,
            available_cash=1_000_000.0,
            base_currency=Currency.HKD,
            fx_rate=lambda _from, _to, _as_of: None,
        )
        return derive_order_drafts(target=target, positions={}, available_cash=1_000_000.0)

    def test_signal_change_changes_drafts(self) -> None:
        first = self._derive((("0001.HK", 0.5), ("0002.HK", 0.5)))
        second = self._derive((("0001.HK", 0.8), ("0002.HK", 0.2)))
        by_symbol_first = {draft.symbol: draft for draft in first.drafts}
        by_symbol_second = {draft.symbol: draft for draft in second.drafts}
        self.assertAlmostEqual(by_symbol_first["0001.HK"].quantity, 10_000.0, places=6)
        self.assertAlmostEqual(by_symbol_second["0001.HK"].quantity, 16_000.0, places=6)

    def test_same_signal_replays_identically(self) -> None:
        first = self._derive((("0001.HK", 0.5), ("0002.HK", 0.5)))
        second = self._derive((("0001.HK", 0.5), ("0002.HK", 0.5)))
        self.assertEqual(first, second)


class TargetDeltaTests(unittest.TestCase):
    """Target vs current deltas produce the right drafts (SP 4.27)."""

    def test_held_symbol_reduces_buy(self) -> None:
        signal = _signal((("0001.HK", 0.5), ("0002.HK", 0.5)), Market.HK, "sig-1")
        target = derive_target_portfolio(
            signal=signal,
            positions={(Market.HK, "0001.HK"): 4_000.0},
            prices={(Market.HK, "0001.HK"): 50.0, (Market.HK, "0002.HK"): 20.0},
            portfolio_value=1_000_000.0,
            available_cash=1_000_000.0,
            base_currency=Currency.HKD,
            fx_rate=lambda _from, _to, _as_of: None,
        )
        result = derive_order_drafts(
            target=target,
            positions={(Market.HK, "0001.HK"): 4_000.0},
            available_cash=1_000_000.0,
        )
        drafts = {draft.symbol: draft for draft in result.drafts}
        self.assertAlmostEqual(drafts["0001.HK"].quantity, 6_000.0, places=6)


class CashShortfallTests(unittest.TestCase):
    """Cash shortfalls are surfaced, never silently executed (SP 4.27)."""

    def test_shortfall_recorded(self) -> None:
        signal = _signal((("0001.HK", 1.0),), Market.HK, "sig-1")
        target = derive_target_portfolio(
            signal=signal,
            positions={},
            prices={(Market.HK, "0001.HK"): 50.0},
            portfolio_value=1_000_000.0,
            available_cash=200_000.0,
            base_currency=Currency.HKD,
            fx_rate=lambda _from, _to, _as_of: None,
        )
        result = derive_order_drafts(target=target, positions={}, available_cash=200_000.0)
        self.assertAlmostEqual(result.buy_value_base, 1_000_000.0, places=6)
        self.assertAlmostEqual(result.cash_shortfall, 800_000.0, places=6)


class MultiMarketConcurrentTests(unittest.TestCase):
    """Concurrent HK/US orders stay in independent batches (SP 4.27 / 4.24)."""

    def test_concurrent_orders_never_merged(self) -> None:
        hk_signal = _signal((("0001.HK", 1.0),), Market.HK, "sig-hk")
        us_signal = _signal((("AAPL", 1.0),), Market.US, "sig-us")
        hk_target = derive_target_portfolio(
            signal=hk_signal,
            positions={},
            prices={(Market.HK, "0001.HK"): 50.0},
            portfolio_value=500_000.0,
            available_cash=1_000_000.0,
            base_currency=Currency.HKD,
            fx_rate=lambda _from, _to, _as_of: None,
        )
        us_target = derive_target_portfolio(
            signal=us_signal,
            positions={},
            prices={(Market.US, "AAPL"): 100.0},
            portfolio_value=500_000.0,
            available_cash=1_000_000.0,
            base_currency=Currency.HKD,
            fx_rate=lambda _from, _to, _as_of: 7.8,
        )
        hk_drafts = derive_order_drafts(target=hk_target, positions={}, available_cash=1_000_000.0)
        us_drafts = derive_order_drafts(target=us_target, positions={}, available_cash=1_000_000.0)
        orders: list[PaperOrder] = []
        for draft in (*hk_drafts.drafts, *us_drafts.drafts):
            orders.append(
                PaperOrder(
                    order_id=f"order-{draft.market.value}-{draft.symbol}",
                    paper_run_id="paper-1",
                    market=draft.market,
                    symbol=draft.symbol,
                    side=draft.side,
                    quantity=draft.quantity,
                    currency=draft.currency,
                    price_type=draft.price_type,
                    status="CREATED",  # type: ignore[arg-type]
                    created_at=_utc_at(2026, 1, 2, 9),
                    intention_id=("sig-hk" if draft.market is Market.HK else "sig-us"),
                )
            )
        batches = group_orders_by_market(orders)
        self.assertEqual([batch.market for batch in batches], [Market.HK, Market.US])
        # each batch contains only its own market's orders
        for batch in batches:
            self.assertTrue(all(order.market is batch.market for order in batch.orders))
