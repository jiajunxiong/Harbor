"""Corporate action backtest tests (MVP 2 / SP 2.44).

Verifies that MVP 1's market-specific corporate-action rules are reused:
splits, consolidations, rights issues, mergers and spin-offs transform the
position quantity, cash actions produce a cash amount, and a market mismatch
is refused (HK and US rules are never mixed).
"""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_domain import Currency, Market, Position
from harbor.core.corporate_actions import PositionAdjustment, apply_corporate_action
from harbor.core.equity import EntitlementEvent, compute_entitlement
from harbor.core.market_registry import CorporateActionType

_DAY = date(2024, 1, 2)


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


_SPLIT = CorporateActionType.SPLIT
_CONSOLIDATION = CorporateActionType.CONSOLIDATION
_RIGHTS = CorporateActionType.RIGHTS_ISSUE
_MERGER = CorporateActionType.MERGER
_SPIN_OFF = CorporateActionType.SPIN_OFF
_TENDER = CorporateActionType.TENDER_OFFER
_DIVIDEND = CorporateActionType.DIVIDEND


class ShareActionTests(unittest.TestCase):
    """Verify share-transforming actions change the position quantity."""

    def test_us_split_doubles_quantity(self) -> None:
        adjustment = apply_corporate_action(
            _position(symbol="AAPL", market=Market.US, quantity=100.0),
            _event(action_type=_SPLIT, ratio=2.0),
        )
        self.assertIsInstance(adjustment, PositionAdjustment)
        self.assertAlmostEqual(adjustment.new_quantity, 200.0)
        self.assertEqual(adjustment.cash_amount, 0.0)
        self.assertTrue(adjustment.shares_changed)

    def test_hk_consolidation_reduces_quantity(self) -> None:
        adjustment = apply_corporate_action(
            _position(quantity=100.0), _event(action_type=_CONSOLIDATION, ratio=0.25)
        )
        self.assertAlmostEqual(adjustment.new_quantity, 25.0)

    def test_hk_rights_issue_adds_shares(self) -> None:
        adjustment = apply_corporate_action(
            _position(quantity=100.0), _event(action_type=_RIGHTS, ratio=0.5)
        )
        self.assertAlmostEqual(adjustment.new_quantity, 50.0)

    def test_us_merger_one_for_one(self) -> None:
        adjustment = apply_corporate_action(
            _position(symbol="AAPL", market=Market.US, quantity=100.0),
            _event(action_type=_MERGER, ratio=1.0),
        )
        self.assertAlmostEqual(adjustment.new_quantity, 100.0)
        self.assertFalse(adjustment.shares_changed)

    def test_us_spin_off_grants_new_shares(self) -> None:
        adjustment = apply_corporate_action(
            _position(symbol="AAPL", market=Market.US, quantity=100.0),
            _event(action_type=_SPIN_OFF, ratio=0.2),
        )
        self.assertAlmostEqual(adjustment.new_quantity, 20.0)


class CashActionTests(unittest.TestCase):
    """Verify cash actions leave the quantity unchanged."""

    def test_hk_tender_offer_pays_cash(self) -> None:
        adjustment = apply_corporate_action(
            _position(quantity=100.0), _event(action_type=_TENDER, price=12.0)
        )
        self.assertAlmostEqual(adjustment.new_quantity, 100.0)
        self.assertAlmostEqual(adjustment.cash_amount, 1_200.0)
        self.assertFalse(adjustment.shares_changed)

    def test_hk_dividend_pays_cash(self) -> None:
        adjustment = apply_corporate_action(
            _position(quantity=100.0), _event(action_type=_DIVIDEND, price=1.0)
        )
        self.assertAlmostEqual(adjustment.new_quantity, 100.0)
        self.assertAlmostEqual(adjustment.cash_amount, 100.0)


class MarketSpecificTests(unittest.TestCase):
    """Verify HK and US corporate-action rules are never mixed."""

    def test_split_on_hk_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for HK"):
            apply_corporate_action(_position(), _event(action_type=_SPLIT, ratio=2.0))

    def test_consolidation_on_us_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for US"):
            apply_corporate_action(
                _position(symbol="AAPL", market=Market.US),
                _event(action_type=_CONSOLIDATION, ratio=0.25),
            )

    def test_rights_issue_on_us_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for US"):
            apply_corporate_action(
                _position(symbol="AAPL", market=Market.US),
                _event(action_type=_RIGHTS, ratio=0.5),
            )

    def test_merger_on_hk_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for HK"):
            apply_corporate_action(_position(), _event(action_type=_MERGER, ratio=1.0))

    def test_spin_off_on_hk_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for HK"):
            apply_corporate_action(_position(), _event(action_type=_SPIN_OFF, ratio=0.2))


class ValidationTests(unittest.TestCase):
    """Verify term and quantity validation."""

    def test_share_action_requires_ratio(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive ratio"):
            apply_corporate_action(
                _position(symbol="AAPL", market=Market.US),
                _event(action_type=_SPLIT),
            )

    def test_cash_action_requires_price(self) -> None:
        with self.assertRaisesRegex(ValueError, "price"):
            apply_corporate_action(_position(), _event(action_type=_TENDER))

    def test_negative_quantity_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            apply_corporate_action(
                _position(symbol="AAPL", market=Market.US, quantity=-1.0),
                _event(action_type=_SPLIT, ratio=2.0),
            )


class ReuseAndMetadataTests(unittest.TestCase):
    """Verify MVP 1 entitlement reuse and the adjustment metadata."""

    def test_reuses_mvp1_entitlement(self) -> None:
        position = _position(symbol="AAPL", market=Market.US, quantity=100.0)
        event = _event(action_type=_SPLIT, ratio=2.0)
        entitled, cash = compute_entitlement(MarketTarget.US, "AAPL", 100.0, event)
        adjustment = apply_corporate_action(position, event)
        self.assertAlmostEqual(adjustment.new_quantity, entitled)
        self.assertEqual(adjustment.cash_amount, cash)

    def test_metadata(self) -> None:
        adjustment = apply_corporate_action(
            _position(symbol="AAPL", market=Market.US, quantity=100.0),
            _event(action_type=_SPLIT, action_id="act-9", ratio=2.0),
        )
        self.assertEqual(adjustment.market, Market.US)
        self.assertEqual(adjustment.symbol, "AAPL")
        self.assertEqual(adjustment.action_id, "act-9")
        self.assertEqual(adjustment.action_type, _SPLIT)
        self.assertAlmostEqual(adjustment.old_quantity, 100.0)

    def test_readable_summary(self) -> None:
        adjustment = apply_corporate_action(
            _position(quantity=100.0), _event(action_type=_TENDER, price=12.0)
        )
        summary = adjustment.readable()
        self.assertIn("tender_offer (act-1) on 0001.HK", summary)
        self.assertIn("100.00 -> 100.00 shares", summary)
        self.assertIn("cash: 1200.00", summary)

    def test_position_is_unchanged(self) -> None:
        position = _position(symbol="AAPL", market=Market.US, quantity=100.0)
        apply_corporate_action(position, _event(action_type=_SPLIT, ratio=2.0))
        self.assertAlmostEqual(position.quantity, 100.0)


if __name__ == "__main__":
    unittest.main()
