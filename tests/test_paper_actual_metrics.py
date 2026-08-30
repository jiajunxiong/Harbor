"""Paper actual execution metrics tests (MVP 4 / SP 4.70).

Verifies that the observed fill price, realized slippage, spread, latency and
participation are collected exactly as executed.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_actual_metrics import (
    PaperActualMetricsError,
    collect_paper_actual_metrics,
    realized_slippage_bps,
)
from harbor.core.paper_domain import PaperFill

_TRADE = date(2026, 1, 2)


def _fill(
    price: float = 50.0,
    quantity: float = 100.0,
    fee: float = 5.0,
    side: OrderSide = OrderSide.BUY,
) -> PaperFill:
    return PaperFill(
        fill_id="fill-1",
        paper_order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=side,
        quantity=quantity,
        price=price,
        currency=Currency.HKD,
        trade_date=_TRADE,
        fee=fee,
    )


class RealizedSlippageTests(unittest.TestCase):
    """The realized slippage derivation (SP 4.70)."""

    def test_buy_pays_up(self) -> None:
        slippage = realized_slippage_bps(_fill(price=50.5), reference_price=50.0)
        self.assertAlmostEqual(slippage, 100.0)  # 0.5 / 50 * 10000

    def test_sell_receives_less(self) -> None:
        fill = _fill(price=49.5, side=OrderSide.SELL)
        slippage = realized_slippage_bps(fill, reference_price=50.0)
        self.assertAlmostEqual(slippage, 100.0)

    def test_zero_slippage_at_reference(self) -> None:
        self.assertAlmostEqual(realized_slippage_bps(_fill(), 50.0), 0.0)

    def test_invalid_reference_rejected(self) -> None:
        with self.assertRaises(PaperActualMetricsError):
            realized_slippage_bps(_fill(), 0.0)


class CollectActualMetricsTests(unittest.TestCase):
    """The actual execution metric collection (SP 4.70)."""

    def test_collects_fields(self) -> None:
        actual = collect_paper_actual_metrics(
            paper_run_id="paper-1",
            fill=_fill(price=50.5, quantity=100.0),
            reference_price=50.0,
            spread_bps=4.0,
            execution_latency_seconds=6.0,
            day_volume=10_000,
        )
        self.assertAlmostEqual(actual.slippage_bps, 100.0)
        self.assertEqual(actual.spread_bps, 4.0)
        self.assertEqual(actual.execution_latency_seconds, 6.0)
        self.assertAlmostEqual(actual.participation_rate, 0.01)
        self.assertEqual(actual.quantity, 100.0)
        self.assertEqual(actual.fee, 5.0)

    def test_readable(self) -> None:
        actual = collect_paper_actual_metrics(
            paper_run_id="paper-1",
            fill=_fill(),
            reference_price=50.0,
            spread_bps=1.0,
            execution_latency_seconds=2.0,
            day_volume=10_000,
        )
        self.assertIn("0001.HK on 2026-01-02", actual.readable())

    def test_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(PaperActualMetricsError):
            collect_paper_actual_metrics(
                paper_run_id="",
                fill=_fill(),
                reference_price=50.0,
                spread_bps=0.0,
                execution_latency_seconds=0.0,
                day_volume=100,
            )
        with self.assertRaises(PaperActualMetricsError):
            collect_paper_actual_metrics(
                paper_run_id="paper-1",
                fill=_fill(),
                reference_price=0.0,
                spread_bps=0.0,
                execution_latency_seconds=0.0,
                day_volume=100,
            )
        with self.assertRaises(PaperActualMetricsError):
            collect_paper_actual_metrics(
                paper_run_id="paper-1",
                fill=_fill(),
                reference_price=50.0,
                spread_bps=-1.0,
                execution_latency_seconds=0.0,
                day_volume=100,
            )
        with self.assertRaises(PaperActualMetricsError):
            collect_paper_actual_metrics(
                paper_run_id="paper-1",
                fill=_fill(),
                reference_price=50.0,
                spread_bps=0.0,
                execution_latency_seconds=-1.0,
                day_volume=100,
            )

    def test_fill_exceeding_volume_rejected(self) -> None:
        with self.assertRaises(PaperActualMetricsError):
            collect_paper_actual_metrics(
                paper_run_id="paper-1",
                fill=_fill(quantity=500.0),
                reference_price=50.0,
                spread_bps=0.0,
                execution_latency_seconds=0.0,
                day_volume=100,
            )
