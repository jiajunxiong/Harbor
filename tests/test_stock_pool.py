"""Historical stock pool contract tests (MVP 2 / SP 2.10)."""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.stock_pool import (
    StockPoolMembership,
    evaluate_stock_pool,
    is_active_on,
)


def _membership(
    symbol: str,
    effective: date | None,
    expiry: date | None = None,
) -> StockPoolMembership:
    return StockPoolMembership(
        market=Market.HK,
        symbol=symbol,
        effective_date=effective,
        expiry_date=expiry,
        source="hkex_universe",
    )


class MembershipActivationTests(unittest.TestCase):
    """Verify the as-of membership window semantics."""

    def test_active_when_listed_and_not_delisted(self) -> None:
        membership = _membership("0001.HK", date(2010, 1, 1))
        self.assertTrue(is_active_on(membership, date(2026, 1, 2)))
        self.assertFalse(is_active_on(membership, date(2009, 12, 31)))

    def test_active_on_effective_and_expiry_boundaries(self) -> None:
        membership = _membership("0001.HK", date(2010, 1, 1), date(2020, 1, 1))
        self.assertTrue(is_active_on(membership, date(2010, 1, 1)))
        self.assertTrue(is_active_on(membership, date(2020, 1, 1)))
        self.assertFalse(is_active_on(membership, date(2020, 1, 2)))

    def test_unknown_effective_date_is_not_active(self) -> None:
        membership = _membership("0001.HK", None)
        self.assertFalse(is_active_on(membership, date(2026, 1, 2)))


class StockPoolEvaluationTests(unittest.TestCase):
    """Verify the as-of pool and survivorship-bias risk marking."""

    def test_evaluates_active_memberships_and_sorted_symbols(self) -> None:
        pool = evaluate_stock_pool(
            Market.HK,
            date(2026, 1, 2),
            [
                _membership("0002.HK", date(2010, 1, 1)),
                _membership("0001.HK", date(2010, 1, 1)),
                _membership("OLD.HK", date(2010, 1, 1), date(2019, 12, 31)),
            ],
            "hkex_universe",
            historical_known=True,
        )
        self.assertEqual(pool.symbols, ("0001.HK", "0002.HK"))
        self.assertEqual(len(pool.memberships), 2)

    def test_no_risk_when_historical_constituents_are_known(self) -> None:
        pool = evaluate_stock_pool(
            Market.HK,
            date(2026, 1, 2),
            [_membership("0001.HK", date(2010, 1, 1))],
            "hkex_universe",
            historical_known=True,
        )
        self.assertFalse(pool.survivorship_bias_risk)
        self.assertIsNone(pool.risk_reason)

    def test_risk_when_source_not_historical(self) -> None:
        pool = evaluate_stock_pool(
            Market.HK,
            date(2026, 1, 2),
            [_membership("0001.HK", date(2010, 1, 1))],
            "hkex_universe",
            historical_known=False,
        )
        self.assertTrue(pool.survivorship_bias_risk)
        self.assertIn("does not guarantee historical constituents", pool.risk_reason or "")

    def test_risk_when_no_membership_active(self) -> None:
        pool = evaluate_stock_pool(
            Market.HK,
            date(2026, 1, 2),
            [_membership("OLD.HK", date(2010, 1, 1), date(2019, 12, 31))],
            "hkex_universe",
            historical_known=True,
        )
        self.assertTrue(pool.survivorship_bias_risk)
        self.assertIn("no memberships are active", pool.risk_reason or "")
        self.assertEqual(pool.symbols, ())

    def test_risk_when_membership_lacks_inclusion_date(self) -> None:
        pool = evaluate_stock_pool(
            Market.HK,
            date(2026, 1, 2),
            [_membership("0001.HK", date(2010, 1, 1)), _membership("UNDATED.HK", None)],
            "hkex_universe",
            historical_known=True,
        )
        self.assertTrue(pool.survivorship_bias_risk)
        self.assertIn("lack an inclusion", pool.risk_reason or "")

    def test_risk_reason_prioritizes_historical_unknown(self) -> None:
        pool = evaluate_stock_pool(
            Market.HK,
            date(2026, 1, 2),
            [],
            "hkex_universe",
            historical_known=False,
        )
        self.assertTrue(pool.survivorship_bias_risk)
        self.assertIn("does not guarantee historical constituents", pool.risk_reason or "")


if __name__ == "__main__":
    unittest.main()
