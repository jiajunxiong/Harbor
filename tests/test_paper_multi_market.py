"""Multi-market paper orchestration tests (MVP 4 / SP 4.24).

Verifies that HK/US orders are grouped into independent per-market batches and
never implicitly merged, and that the single-market gate refuses a mixed batch.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_domain import (
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
)
from harbor.core.paper_multi_market import (
    MarketOrderBatch,
    PaperMarketError,
    assert_single_market,
    group_orders_by_market,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _order(order_id: str, market: Market, symbol: str) -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        paper_run_id="paper-1",
        market=market,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=100.0,
        currency=Currency.HKD if market is Market.HK else Currency.USD,
        price_type=PaperPriceType.REFERENCE,
        status=PaperOrderStatus.CREATED,
        created_at=_utc_at(2026, 1, 2, 9),
        intention_id="sig-1",
    )


class GroupOrdersByMarketTests(unittest.TestCase):
    """Independent per-market batches (SP 4.24)."""

    def test_groups_by_market(self) -> None:
        orders = [
            _order("o-hk-1", Market.HK, "0001.HK"),
            _order("o-us-1", Market.US, "AAPL"),
            _order("o-hk-2", Market.HK, "0002.HK"),
        ]
        batches = group_orders_by_market(orders)
        self.assertEqual([batch.market for batch in batches], [Market.HK, Market.US])
        self.assertEqual([order.order_id for order in batches[0].orders], ["o-hk-1", "o-hk-2"])
        self.assertEqual([order.order_id for order in batches[1].orders], ["o-us-1"])

    def test_single_market_batch(self) -> None:
        orders = [_order("o-hk-1", Market.HK, "0001.HK"), _order("o-hk-2", Market.HK, "0002.HK")]
        batches = group_orders_by_market(orders)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].market, Market.HK)

    def test_empty(self) -> None:
        self.assertEqual(group_orders_by_market([]), ())

    def test_batch_validates_market(self) -> None:
        with self.assertRaises(PaperMarketError):
            MarketOrderBatch(
                market=Market.HK,
                orders=(_order("o-us-1", Market.US, "AAPL"),),
            )

    def test_batch_readable(self) -> None:
        batch = MarketOrderBatch(market=Market.HK, orders=(_order("o-hk-1", Market.HK, "0001.HK"),))
        self.assertIn("HK batch: 1 order(s)", batch.readable())


class AssertSingleMarketTests(unittest.TestCase):
    """The single-market gate (SP 4.24)."""

    def test_single_market_passes(self) -> None:
        orders = [_order("o-hk-1", Market.HK, "0001.HK")]
        self.assertEqual(assert_single_market(orders), Market.HK)

    def test_mixed_market_refused(self) -> None:
        orders = [
            _order("o-hk-1", Market.HK, "0001.HK"),
            _order("o-us-1", Market.US, "AAPL"),
        ]
        with self.assertRaises(PaperMarketError) as ctx:
            assert_single_market(orders)
        self.assertIn("multiple markets", str(ctx.exception))

    def test_empty_refused(self) -> None:
        with self.assertRaises(PaperMarketError):
            assert_single_market([])
