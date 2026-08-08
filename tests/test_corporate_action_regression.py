"""Corporate action regression tests (MVP 2 / SP 2.81).

Locks in MVP 1's market-specific corporate-action rules so they can never be
silently replaced by a single unified logic (不得以统一逻辑替代). It verifies
that:

- the HK and US allowed action-type sets match the MVP 1 registry (SP 1.34) —
  HK 供股/合股/要约/股息, US 拆股/并购/分拆/股息 — and that the two sets differ;
- every US-only action type is refused on an HK position and vice versa (the
  HK and US rules are never mixed, SP 2.44);
- the backtest corporate-action layer (SP 2.44) produces the SAME entitled
  quantity and cash amount as MVP 1's ``compute_equity_entitlement`` (SP
  1.79/1.80) for the same event — proving reuse, not a new logic;
- the entitlement math for all seven action types (share vs cash) and the
  missing-term / negative-quantity failures are stable.

The suite is self-contained; no database is required.
"""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.action_mapping import allowed_action_types
from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_domain import Currency, Market, Position
from harbor.core.corporate_actions import PositionAdjustment, apply_corporate_action
from harbor.core.equity import EntitlementEvent, compute_entitlement, compute_equity_entitlement
from harbor.core.market_registry import CorporateActionType, get_market_config

_DAY = date(2024, 1, 2)

_SPLIT = CorporateActionType.SPLIT
_CONSOLIDATION = CorporateActionType.CONSOLIDATION
_RIGHTS = CorporateActionType.RIGHTS_ISSUE
_MERGER = CorporateActionType.MERGER
_SPIN_OFF = CorporateActionType.SPIN_OFF
_TENDER = CorporateActionType.TENDER_OFFER
_DIVIDEND = CorporateActionType.DIVIDEND

_HK_ONLY = frozenset({_RIGHTS, _CONSOLIDATION, _TENDER})
_US_ONLY = frozenset({_SPLIT, _MERGER, _SPIN_OFF})


def _position(
    *,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    quantity: float = 100.0,
) -> Position:
    currency = Currency.HKD if market is Market.HK else Currency.USD
    return Position(
        symbol=symbol,
        market=market,
        quantity=quantity,
        average_cost=10.0,
        currency=currency,
        as_of_date=_DAY,
    )


def _event(
    *,
    action_type: CorporateActionType,
    action_id: str = "act-1",
    ratio: float | None = None,
    price: float | None = None,
) -> EntitlementEvent:
    return EntitlementEvent(
        action_id=action_id,
        action_type=action_type,
        terms=ActionTerms(ratio=ratio, price=price),
        record_date=_DAY,
        ex_date=_DAY,
    )


def _mvp1_result(
    market: MarketTarget,
    symbol: str,
    quantity: float,
    event: EntitlementEvent,
) -> tuple[float, float]:
    """Return MVP 1's (entitled_quantity, cash_amount) for an event."""
    rows = compute_equity_entitlement(market, symbol, _DAY, quantity, [event])
    return (rows[0]["entitled_quantity"], rows[0]["cash_amount"])


class MarketRuleSetRegressionTests(unittest.TestCase):
    """Anti-drift: the per-market rule sets match the MVP 1 registry."""

    def test_hk_allowed_set_matches_mvp1(self) -> None:
        expected = frozenset({_RIGHTS, _CONSOLIDATION, _TENDER, _DIVIDEND})
        self.assertEqual(allowed_action_types(MarketTarget.HK), expected)
        self.assertEqual(get_market_config(MarketTarget.HK).corporate_action_types, expected)

    def test_us_allowed_set_matches_mvp1(self) -> None:
        expected = frozenset({_SPLIT, _MERGER, _SPIN_OFF, _DIVIDEND})
        self.assertEqual(allowed_action_types(MarketTarget.US), expected)
        self.assertEqual(get_market_config(MarketTarget.US).corporate_action_types, expected)

    def test_markets_are_not_unified(self) -> None:
        """The two markets keep distinct, non-overlapping share-action sets."""
        hk = allowed_action_types(MarketTarget.HK)
        us = allowed_action_types(MarketTarget.US)
        self.assertNotEqual(hk, us)
        # Dividend is the only type shared by both markets.
        self.assertEqual(hk & us, {_DIVIDEND})
        # HK carries none of the US share actions; US carries none of HK's.
        self.assertTrue(_HK_ONLY.issubset(hk))
        self.assertTrue(_US_ONLY.issubset(us))
        self.assertTrue(_HK_ONLY.isdisjoint(us))
        self.assertTrue(_US_ONLY.isdisjoint(hk))


class MarketAllowanceMatrixTests(unittest.TestCase):
    """HK and US corporate-action rules are never mixed (SP 2.44)."""

    def test_every_us_only_type_refused_on_hk(self) -> None:
        for action_type in sorted(_US_ONLY, key=lambda item: item.value):
            with self.subTest(action_type=action_type):
                with self.assertRaisesRegex(ValueError, "not supported for HK"):
                    apply_corporate_action(
                        _position(),
                        _event(action_type=action_type, ratio=2.0),
                    )

    def test_every_hk_only_type_refused_on_us(self) -> None:
        for action_type in sorted(_HK_ONLY, key=lambda item: item.value):
            with self.subTest(action_type=action_type):
                with self.assertRaisesRegex(ValueError, "not supported for US"):
                    apply_corporate_action(
                        _position(symbol="AAPL", market=Market.US),
                        _event(action_type=action_type, ratio=2.0),
                    )

    def test_compute_entitlement_refuses_cross_market(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for HK"):
            compute_entitlement(
                MarketTarget.HK,
                "0001.HK",
                100.0,
                _event(action_type=_SPLIT, ratio=2.0),
            )
        with self.assertRaisesRegex(ValueError, "not supported for US"):
            compute_entitlement(
                MarketTarget.US,
                "AAPL",
                100.0,
                _event(action_type=_RIGHTS, ratio=0.5),
            )


class Mvp1BacktestEquivalenceTests(unittest.TestCase):
    """The backtest reuses MVP 1's entitlement results, not a new logic."""

    def test_share_actions_match_mvp1(self) -> None:
        cases = [
            (MarketTarget.US, "AAPL", _SPLIT, 2.0, None),
            (MarketTarget.HK, "0001.HK", _CONSOLIDATION, 0.25, None),
            (MarketTarget.HK, "0001.HK", _RIGHTS, 0.5, None),
            (MarketTarget.US, "AAPL", _MERGER, 1.0, None),
            (MarketTarget.US, "AAPL", _SPIN_OFF, 0.2, None),
        ]
        for market_target, symbol, action_type, ratio, price in cases:
            with self.subTest(action_type=action_type):
                event = _event(action_type=action_type, ratio=ratio, price=price)
                backtest = compute_entitlement(market_target, symbol, 100.0, event)
                mvp1 = _mvp1_result(market_target, symbol, 100.0, event)
                self.assertEqual(backtest, mvp1)
                adjustment = apply_corporate_action(
                    _position(
                        symbol=symbol,
                        market=Market(market_target.value),
                        quantity=100.0,
                    ),
                    event,
                )
                self.assertAlmostEqual(adjustment.new_quantity, mvp1[0], places=2)
                self.assertAlmostEqual(adjustment.cash_amount, mvp1[1], places=2)

    def test_cash_actions_match_mvp1(self) -> None:
        cases = [
            (MarketTarget.HK, "0001.HK", _DIVIDEND, None, 1.0),
            (MarketTarget.US, "AAPL", _DIVIDEND, None, 0.5),
            (MarketTarget.HK, "0001.HK", _TENDER, None, 12.0),
        ]
        for market_target, symbol, action_type, ratio, price in cases:
            with self.subTest(action_type=action_type):
                event = _event(action_type=action_type, ratio=ratio, price=price)
                backtest = compute_entitlement(market_target, symbol, 100.0, event)
                mvp1 = _mvp1_result(market_target, symbol, 100.0, event)
                self.assertEqual(backtest, mvp1)
                adjustment = apply_corporate_action(
                    _position(
                        symbol=symbol,
                        market=Market(market_target.value),
                        quantity=100.0,
                    ),
                    event,
                )
                self.assertEqual(adjustment.new_quantity, 100.0)
                self.assertAlmostEqual(adjustment.cash_amount, mvp1[1], places=2)

    def test_backtest_result_matches_mvp1_for_every_market(self) -> None:
        """All seven types produce identical results through both paths."""
        for market_target, action_type, terms in (
            (MarketTarget.HK, _RIGHTS, dict(ratio=0.5)),
            (MarketTarget.HK, _CONSOLIDATION, dict(ratio=0.25)),
            (MarketTarget.HK, _TENDER, dict(price=12.0)),
            (MarketTarget.HK, _DIVIDEND, dict(price=1.0)),
            (MarketTarget.US, _SPLIT, dict(ratio=2.0)),
            (MarketTarget.US, _MERGER, dict(ratio=1.0)),
            (MarketTarget.US, _SPIN_OFF, dict(ratio=0.2)),
            (MarketTarget.US, _DIVIDEND, dict(price=0.5)),
        ):
            with self.subTest(action_type=action_type):
                event = _event(action_type=action_type, **terms)
                symbol = "0001.HK" if market_target is MarketTarget.HK else "AAPL"
                self.assertEqual(
                    compute_entitlement(market_target, symbol, 100.0, event),
                    _mvp1_result(market_target, symbol, 100.0, event),
                )


class EntitlementMathRegressionTests(unittest.TestCase):
    """Share vs cash math and required-term failures stay stable."""

    def test_share_actions_multiply_quantity(self) -> None:
        self.assertEqual(
            compute_entitlement(
                MarketTarget.US, "AAPL", 100.0, _event(action_type=_SPLIT, ratio=2.0)
            ),
            (200.0, 0.0),
        )
        self.assertEqual(
            compute_entitlement(
                MarketTarget.HK, "0001.HK", 100.0, _event(action_type=_CONSOLIDATION, ratio=0.25)
            ),
            (25.0, 0.0),
        )
        self.assertEqual(
            compute_entitlement(
                MarketTarget.HK, "0001.HK", 100.0, _event(action_type=_RIGHTS, ratio=0.5)
            ),
            (50.0, 0.0),
        )

    def test_cash_actions_pay_cash_and_keep_quantity(self) -> None:
        self.assertEqual(
            compute_entitlement(
                MarketTarget.HK, "0001.HK", 100.0, _event(action_type=_DIVIDEND, price=1.0)
            ),
            (0.0, 100.0),
        )
        self.assertEqual(
            compute_entitlement(
                MarketTarget.HK, "0001.HK", 100.0, _event(action_type=_TENDER, price=12.0)
            ),
            (0.0, 1_200.0),
        )

    def test_share_action_requires_a_ratio(self) -> None:
        for action_type in (_SPLIT, _CONSOLIDATION, _RIGHTS, _MERGER, _SPIN_OFF):
            with self.subTest(action_type=action_type):
                with self.assertRaisesRegex(ValueError, "positive ratio term"):
                    compute_entitlement(
                        MarketTarget.US if action_type in _US_ONLY else MarketTarget.HK,
                        "AAPL" if action_type in _US_ONLY else "0001.HK",
                        100.0,
                        _event(action_type=action_type),
                    )

    def test_cash_action_requires_a_price(self) -> None:
        for action_type in (_DIVIDEND, _TENDER):
            with self.subTest(action_type=action_type):
                with self.assertRaisesRegex(ValueError, "requires a price term"):
                    compute_entitlement(
                        MarketTarget.HK,
                        "0001.HK",
                        100.0,
                        _event(action_type=action_type),
                    )

    def test_negative_quantity_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            compute_entitlement(
                MarketTarget.HK,
                "0001.HK",
                -1.0,
                _event(action_type=_DIVIDEND, price=1.0),
            )


class AdjustmentRecordRegressionTests(unittest.TestCase):
    """PositionAdjustment carries the market identity and action details."""

    def test_adjustment_records_market_and_action(self) -> None:
        adjustment = apply_corporate_action(
            _position(symbol="0001.HK", quantity=100.0),
            _event(action_type=_RIGHTS, action_id="act-9", ratio=0.5),
        )
        self.assertIsInstance(adjustment, PositionAdjustment)
        self.assertEqual(adjustment.market, Market.HK)
        self.assertEqual(adjustment.symbol, "0001.HK")
        self.assertEqual(adjustment.action_id, "act-9")
        self.assertEqual(adjustment.action_type, _RIGHTS)
        self.assertEqual(adjustment.old_quantity, 100.0)
        self.assertIn("0001.HK", adjustment.readable())
        self.assertIn("act-9", adjustment.readable())

    def test_shares_changed_flag(self) -> None:
        changed = apply_corporate_action(
            _position(quantity=100.0),
            _event(action_type=_RIGHTS, ratio=0.5),
        )
        unchanged = apply_corporate_action(
            _position(symbol="AAPL", market=Market.US, quantity=100.0),
            _event(action_type=_MERGER, ratio=1.0),
        )
        cash = apply_corporate_action(
            _position(quantity=100.0),
            _event(action_type=_DIVIDEND, price=1.0),
        )
        self.assertTrue(changed.shares_changed)
        self.assertFalse(unchanged.shares_changed)
        self.assertFalse(cash.shares_changed)


if __name__ == "__main__":
    unittest.main()
