"""Paper account event tests (MVP 4 / SP 4.53-4.55).

Verifies that dividends are credited in their own currency per the
configuration and that market-specific corporate actions update positions and
cash without mixing HK/US rules.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import DividendConfig
from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.backtest_interfaces import Dividend
from harbor.core.equity import ActionTerms, EntitlementEvent
from harbor.core.market_registry import CorporateActionType
from harbor.core.paper_account import apply_paper_fill, open_paper_account
from harbor.core.paper_account_events import (
    PaperAccountEventError,
    apply_paper_corporate_action,
    apply_paper_dividend,
    credit_paper_cash,
)
from harbor.core.paper_domain import PaperFill

_TRADE = date(2026, 1, 2)


def _open():
    return open_paper_account(
        account_id="acc-1",
        base_currency=Currency.HKD,
        currencies=(Currency.HKD, Currency.USD),
        initial_capital=1_000_000.0,
        opened_at=date(2026, 1, 1),
    )


def _usd_open():
    """Return an account with USD cash funded from HKD."""
    from harbor.core.paper_account import convert_paper_cash

    state = _open()
    return convert_paper_cash(
        state,
        from_currency=Currency.HKD,
        to_currency=Currency.USD,
        amount=200_000.0,
        rate=0.128,
    )


def _fill(symbol: str, market: Market, currency: Currency, quantity: float) -> PaperFill:
    return PaperFill(
        fill_id=f"fill-{symbol}",
        paper_order_id=f"order-{symbol}",
        paper_run_id="paper-1",
        market=market,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=quantity,
        price=50.0,
        currency=currency,
        trade_date=_TRADE,
        fee=0.0,
    )


class DividendTests(unittest.TestCase):
    """Cash dividend processing (SP 4.54)."""

    def test_dividend_credited_in_own_currency(self) -> None:
        state = _open()
        dividend = Dividend(
            market=Market.HK,
            symbol="0001.HK",
            amount=2.0,
            currency=Currency.HKD,
            ex_date=date(2026, 1, 10),
            record_date=date(2026, 1, 8),
            payment_date=date(2026, 1, 20),
            is_special=False,
        )
        updated, payment = apply_paper_dividend(state, dividend=dividend, quantity=100.0)
        self.assertIsNotNone(payment)
        self.assertEqual(payment.gross_amount, 200.0)
        self.assertEqual(updated.balance(Currency.HKD), 1_000_000.0 + 200.0)

    def test_special_dividend_excluded_by_config(self) -> None:
        state = _open()
        dividend = Dividend(
            market=Market.HK,
            symbol="0001.HK",
            amount=5.0,
            currency=Currency.HKD,
            ex_date=date(2026, 1, 10),
            is_special=True,
        )
        updated, payment = apply_paper_dividend(
            state,
            dividend=dividend,
            quantity=100.0,
            config=DividendConfig(include_special=False),
        )
        self.assertIsNone(payment)
        self.assertEqual(updated.balance(Currency.HKD), 1_000_000.0)

    def test_special_dividend_included_by_default(self) -> None:
        state = _open()
        dividend = Dividend(
            market=Market.HK,
            symbol="0001.HK",
            amount=5.0,
            currency=Currency.HKD,
            ex_date=date(2026, 1, 10),
            is_special=True,
        )
        _, payment = apply_paper_dividend(state, dividend=dividend, quantity=100.0)
        self.assertIsNotNone(payment)
        self.assertTrue(payment.is_special)

    def test_zero_quantity_no_payment(self) -> None:
        state = _open()
        dividend = Dividend(
            market=Market.HK,
            symbol="0001.HK",
            amount=2.0,
            currency=Currency.HKD,
            ex_date=date(2026, 1, 10),
        )
        _, payment = apply_paper_dividend(state, dividend=dividend, quantity=0.0)
        self.assertIsNone(payment)

    def test_credit_paper_cash(self) -> None:
        state = _open()
        updated = credit_paper_cash(state, currency=Currency.USD, amount=100.0)
        self.assertEqual(updated.balance(Currency.USD), 100.0)
        self.assertEqual(updated.balance(Currency.HKD), 1_000_000.0)


class CorporateActionTests(unittest.TestCase):
    """Market-specific corporate actions (SP 4.55)."""

    def test_us_split_changes_quantity(self) -> None:
        state = _usd_open()
        state = apply_paper_fill(state, fill=_fill("AAPL", Market.US, Currency.USD, 100.0))
        event = EntitlementEvent(
            action_id="ca-1",
            action_type=CorporateActionType.SPLIT,
            terms=ActionTerms(ratio=2.0),
            record_date=_TRADE,
        )
        updated, adjustment = apply_paper_corporate_action(
            state, market=Market.US, symbol="AAPL", event=event, as_of_date=_TRADE
        )
        self.assertTrue(adjustment.shares_changed)
        self.assertEqual(updated.position(Market.US, "AAPL").quantity, 200.0)
        self.assertEqual(adjustment.cash_amount, 0.0)

    def test_hk_rights_issue(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill("0001.HK", Market.HK, Currency.HKD, 100.0))
        event = EntitlementEvent(
            action_id="ca-2",
            action_type=CorporateActionType.RIGHTS_ISSUE,
            terms=ActionTerms(ratio=0.5),
            record_date=_TRADE,
        )
        updated, adjustment = apply_paper_corporate_action(
            state, market=Market.HK, symbol="0001.HK", event=event, as_of_date=_TRADE
        )
        self.assertEqual(updated.position(Market.HK, "0001.HK").quantity, 50.0)

    def test_dividend_cash_action_credits_ledger(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill("0001.HK", Market.HK, Currency.HKD, 100.0))
        event = EntitlementEvent(
            action_id="ca-3",
            action_type=CorporateActionType.DIVIDEND,
            terms=ActionTerms(price=1.5),
            record_date=_TRADE,
        )
        updated, adjustment = apply_paper_corporate_action(
            state, market=Market.HK, symbol="0001.HK", event=event, as_of_date=_TRADE
        )
        self.assertFalse(adjustment.shares_changed)
        self.assertEqual(adjustment.cash_amount, 150.0)
        self.assertEqual(updated.balance(Currency.HKD), 1_000_000.0 - 5_000.0 + 150.0)

    def test_unheld_position_rejected(self) -> None:
        state = _open()
        event = EntitlementEvent(
            action_id="ca-4",
            action_type=CorporateActionType.SPLIT,
            terms=ActionTerms(ratio=2.0),
            record_date=_TRADE,
        )
        with self.assertRaises(PaperAccountEventError):
            apply_paper_corporate_action(
                state, market=Market.US, symbol="AAPL", event=event, as_of_date=_TRADE
            )

    def test_market_rules_never_mixed(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill("0001.HK", Market.HK, Currency.HKD, 100.0))
        # SPLIT is a US-only action; applying it to an HK position is refused
        event = EntitlementEvent(
            action_id="ca-5",
            action_type=CorporateActionType.SPLIT,
            terms=ActionTerms(ratio=2.0),
            record_date=_TRADE,
        )
        with self.assertRaises(ValueError):
            apply_paper_corporate_action(
                state, market=Market.HK, symbol="0001.HK", event=event, as_of_date=_TRADE
            )
