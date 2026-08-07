"""Corporate-action backtest integration tests (MVP 2 / SP 2.50).

Integrates the dividend processing (SP 2.43), the corporate-action position
transformation (SP 2.44) and the portfolio net-value valuation (SP 2.45) to
verify that dividends, splits, consolidations, rights issues and mergers
impact positions, cash and net value as expected, and that each market keeps
its own corporate-action rules (HK and US are never mixed).

The orchestration flow modelled here mirrors the SP 2.47 pipeline stages:
a corporate action transforms the position and (for cash actions) routes cash
to the ledger, then the portfolio is valued.
"""

import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import date

from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_config import DividendConfig
from harbor.core.backtest_domain import Currency, Market, Position
from harbor.core.backtest_interfaces import Dividend
from harbor.core.corporate_actions import PositionAdjustment, apply_corporate_action
from harbor.core.dividend_processing import CashDividend, pay_dividend
from harbor.core.equity import EntitlementEvent
from harbor.core.ledger import Ledger, credit, deposit, empty_ledger
from harbor.core.market_registry import CorporateActionType
from harbor.core.suspension import PositionValuation
from harbor.core.valuation import DailyValuation, value_portfolio

HKD = Currency.HKD
USD = Currency.USD
_DAY = date(2024, 1, 2)
_EX = date(2024, 1, 5)
_PAY = date(2024, 1, 31)

_SPLIT = CorporateActionType.SPLIT
_CONSOLIDATION = CorporateActionType.CONSOLIDATION
_RIGHTS = CorporateActionType.RIGHTS_ISSUE
_MERGER = CorporateActionType.MERGER
_TENDER = CorporateActionType.TENDER_OFFER
_DIVIDEND = CorporateActionType.DIVIDEND


def _position(
    *,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    quantity: float = 100.0,
) -> Position:
    currency = HKD if market is Market.HK else USD
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


def _dividend(
    *,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    amount: float = 1.0,
    currency: Currency = HKD,
    is_special: bool = False,
) -> Dividend:
    return Dividend(
        market=market,
        symbol=symbol,
        amount=amount,
        currency=currency,
        ex_date=_EX,
        record_date=_DAY,
        payment_date=_PAY,
        is_special=is_special,
    )


def _valuation(
    *,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    price: float = 10.0,
) -> PositionValuation:
    return PositionValuation(
        market=market,
        symbol=symbol,
        price=price,
        carried_forward=False,
        day=_DAY,
        warning=None,
    )


def _fx(rate: float | None) -> Callable[[Currency, Currency, date], float | None]:
    def get(from_currency: Currency, to_currency: Currency, day: date) -> float | None:
        return rate

    return get


def _valuations(
    *items: tuple[Market, str, PositionValuation],
) -> dict[tuple[Market, str], PositionValuation]:
    return {(market, symbol): valuation for market, symbol, valuation in items}


def _ledger(*, base: Currency = HKD, initial: float = 50_000.0) -> Ledger:
    """Return a ledger with base-currency cash funded."""
    ledger = empty_ledger(as_of=_DAY, base_currency=base)
    return deposit(ledger, currency=base, amount=initial, base_rate=1.0)


def _apply_corporate_action(
    ledger: Ledger,
    position: Position,
    event: EntitlementEvent,
) -> tuple[Ledger, Position, PositionAdjustment]:
    """Route a corporate action through the SP 2.47 pipeline stages.

    Applies the action to the position and, for cash actions (dividend /
    tender offer), credits the cash amount to the ledger in the position's own
    currency — matching what the orchestration layer does.
    """
    adjustment = apply_corporate_action(position, event)
    updated_ledger = ledger
    if adjustment.cash_amount != 0.0:
        updated_ledger = credit(
            updated_ledger, currency=position.currency, amount=adjustment.cash_amount
        )
    updated_position = replace(position, quantity=adjustment.new_quantity)
    return updated_ledger, updated_position, adjustment


class DividendImpactTests(unittest.TestCase):
    """Verify dividends credit cash and flow into net value (SP 2.43 + 2.45)."""

    def test_regular_dividend_credits_cash_and_increases_net_value(self) -> None:
        ledger = _ledger(initial=10_000.0)
        position = _position(symbol="0001.HK", market=Market.HK, quantity=1_000.0)
        updated, payment = pay_dividend(
            ledger, dividend=_dividend(amount=1.0), quantity=position.quantity
        )
        self.assertIsInstance(payment, CashDividend)
        assert payment is not None
        self.assertEqual(payment.gross_amount, 1_000.0)
        self.assertAlmostEqual(updated.balance(HKD), 11_000.0, places=2)

        valuation = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=updated,
            positions=(position,),
            valuations=_valuations(
                (Market.HK, "0001.HK", _valuation(symbol="0001.HK", price=10.0))
            ),
            fx_rate=_fx(None),
        )
        self.assertAlmostEqual(valuation.net_value.cash, 11_000.0, places=2)
        self.assertAlmostEqual(valuation.net_value.securities_value, 10_000.0, places=2)
        self.assertAlmostEqual(valuation.net_value.total_value, 21_000.0, places=2)

    def test_us_dividend_credits_usd_and_converts_at_fx_in_valuation(self) -> None:
        ledger = _ledger(base=HKD, initial=10_000.0)
        position = _position(symbol="AAPL", market=Market.US, quantity=100.0)
        updated, payment = pay_dividend(
            ledger,
            dividend=_dividend(symbol="AAPL", market=Market.US, amount=0.5, currency=USD),
            quantity=position.quantity,
        )
        assert payment is not None
        self.assertEqual(payment.gross_amount, 50.0)
        self.assertAlmostEqual(updated.balance(USD), 50.0, places=2)

        valuation = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=updated,
            positions=(position,),
            valuations=_valuations(
                (Market.US, "AAPL", _valuation(symbol="AAPL", market=Market.US, price=100.0))
            ),
            fx_rate=_fx(7.8),
        )
        # USD cash 50 -> 390 HKD; AAPL 100 x 100 USD x 7.8 = 78,000 HKD.
        self.assertAlmostEqual(valuation.net_value.cash, 10_390.0, places=2)
        self.assertAlmostEqual(valuation.net_value.securities_value, 78_000.0, places=2)

    def test_special_dividend_excluded_when_config_disallows(self) -> None:
        ledger = _ledger(initial=10_000.0)
        config = DividendConfig(include_special=False)
        updated, payment = pay_dividend(
            ledger,
            dividend=_dividend(amount=1.0, is_special=True),
            quantity=1_000.0,
            config=config,
        )
        self.assertIsNone(payment)
        self.assertAlmostEqual(updated.balance(HKD), 10_000.0, places=2)

    def test_zero_quantity_pays_no_dividend(self) -> None:
        ledger = _ledger(initial=10_000.0)
        updated, payment = pay_dividend(ledger, dividend=_dividend(amount=1.0), quantity=0.0)
        self.assertIsNone(payment)
        self.assertAlmostEqual(updated.balance(HKD), 10_000.0, places=2)


class ShareActionImpactTests(unittest.TestCase):
    """Verify share actions transform positions and net value (SP 2.44 + 2.45)."""

    def test_us_split_doubles_quantity_and_securities_value(self) -> None:
        ledger = _ledger(base=HKD, initial=10_000.0)
        position = _position(symbol="AAPL", market=Market.US, quantity=100.0)
        updated_ledger, position_after, adjustment = _apply_corporate_action(
            ledger, position, _event(action_type=_SPLIT, ratio=2.0)
        )
        self.assertTrue(adjustment.shares_changed)
        self.assertEqual(position_after.quantity, 200.0)
        # No cash from a split; ledger unchanged.
        self.assertAlmostEqual(updated_ledger.balance(HKD), 10_000.0, places=2)

        valuation = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=updated_ledger,
            positions=(position_after,),
            valuations=_valuations(
                (Market.US, "AAPL", _valuation(symbol="AAPL", market=Market.US, price=50.0))
            ),
            fx_rate=_fx(7.8),
        )
        self.assertAlmostEqual(valuation.net_value.securities_value, 78_000.0, places=2)

    def test_split_does_not_change_net_value_at_adjusted_price(self) -> None:
        ledger = _ledger(base=HKD, initial=10_000.0)
        position = _position(symbol="AAPL", market=Market.US, quantity=100.0)
        before = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=(position,),
            valuations=_valuations(
                (Market.US, "AAPL", _valuation(symbol="AAPL", market=Market.US, price=100.0))
            ),
            fx_rate=_fx(7.8),
        )
        _, position_after, _ = _apply_corporate_action(
            ledger, position, _event(action_type=_SPLIT, ratio=2.0)
        )
        after = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=(position_after,),
            valuations=_valuations(
                (Market.US, "AAPL", _valuation(symbol="AAPL", market=Market.US, price=50.0))
            ),
            fx_rate=_fx(7.8),
        )
        self.assertAlmostEqual(after.net_value.total_value, before.net_value.total_value, places=2)

    def test_hk_consolidation_halves_quantity(self) -> None:
        ledger = _ledger(initial=10_000.0)
        position = _position(symbol="0001.HK", market=Market.HK, quantity=1_000.0)
        _, position_after, adjustment = _apply_corporate_action(
            ledger, position, _event(action_type=_CONSOLIDATION, ratio=0.5)
        )
        self.assertTrue(adjustment.shares_changed)
        self.assertEqual(position_after.quantity, 500.0)

    def test_hk_rights_issue_increases_quantity(self) -> None:
        ledger = _ledger(initial=10_000.0)
        position = _position(symbol="0001.HK", market=Market.HK, quantity=1_000.0)
        _, position_after, adjustment = _apply_corporate_action(
            ledger, position, _event(action_type=_RIGHTS, ratio=1.5)
        )
        self.assertTrue(adjustment.shares_changed)
        self.assertEqual(position_after.quantity, 1_500.0)

    def test_us_merger_transforms_quantity(self) -> None:
        ledger = _ledger(base=HKD, initial=10_000.0)
        position = _position(symbol="OLD", market=Market.US, quantity=100.0)
        _, position_after, adjustment = _apply_corporate_action(
            ledger, position, _event(action_type=_MERGER, ratio=0.8)
        )
        self.assertTrue(adjustment.shares_changed)
        self.assertEqual(position_after.quantity, 80.0)
        self.assertEqual(adjustment.cash_amount, 0.0)


class CashActionImpactTests(unittest.TestCase):
    """Verify cash actions produce cash and leave quantity unchanged (SP 2.44)."""

    def test_hk_tender_offer_credits_cash_and_keeps_quantity(self) -> None:
        ledger = _ledger(initial=10_000.0)
        position = _position(symbol="0001.HK", market=Market.HK, quantity=1_000.0)
        updated_ledger, position_after, adjustment = _apply_corporate_action(
            ledger, position, _event(action_type=_TENDER, price=12.0)
        )
        self.assertFalse(adjustment.shares_changed)
        self.assertEqual(position_after.quantity, 1_000.0)
        self.assertAlmostEqual(adjustment.cash_amount, 12_000.0, places=2)
        self.assertAlmostEqual(updated_ledger.balance(HKD), 22_000.0, places=2)

    def test_us_dividend_action_credits_cash(self) -> None:
        ledger = _ledger(base=HKD, initial=10_000.0)
        position = _position(symbol="AAPL", market=Market.US, quantity=100.0)
        updated_ledger, position_after, adjustment = _apply_corporate_action(
            ledger, position, _event(action_type=_DIVIDEND, price=0.5)
        )
        self.assertFalse(adjustment.shares_changed)
        self.assertEqual(position_after.quantity, 100.0)
        self.assertAlmostEqual(adjustment.cash_amount, 50.0, places=2)
        self.assertAlmostEqual(updated_ledger.balance(USD), 50.0, places=2)


class MarketRuleIsolationTests(unittest.TestCase):
    """Verify HK and US corporate-action rules are never mixed (SP 2.44)."""

    def test_us_split_on_hk_position_is_refused(self) -> None:
        position = _position(symbol="0001.HK", market=Market.HK, quantity=1_000.0)
        with self.assertRaisesRegex(ValueError, "not supported"):
            apply_corporate_action(position, _event(action_type=_SPLIT, ratio=2.0))

    def test_hk_consolidation_on_us_position_is_refused(self) -> None:
        position = _position(symbol="AAPL", market=Market.US, quantity=100.0)
        with self.assertRaisesRegex(ValueError, "not supported"):
            apply_corporate_action(position, _event(action_type=_CONSOLIDATION, ratio=0.5))


class NetValueIntegrationTests(unittest.TestCase):
    """Verify the combined effect of events on net value (SP 2.43-2.45)."""

    def test_dividend_and_split_flow_through_to_net_value(self) -> None:
        # HKD base: a HK dividend credits cash; a US split doubles quantity.
        # Valuation reflects both: cash grows, securities value unchanged by
        # the split (price adjusts).
        ledger = _ledger(base=HKD, initial=20_000.0)
        hk_position = _position(symbol="0001.HK", market=Market.HK, quantity=1_000.0)
        us_position = _position(symbol="AAPL", market=Market.US, quantity=100.0)

        ledger, _ = pay_dividend(
            ledger, dividend=_dividend(amount=1.0), quantity=hk_position.quantity
        )
        _, us_position, _ = _apply_corporate_action(
            ledger, us_position, _event(action_type=_SPLIT, ratio=2.0)
        )

        valuation = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=(hk_position, us_position),
            valuations=_valuations(
                (Market.HK, "0001.HK", _valuation(symbol="0001.HK", price=10.0)),
                (Market.US, "AAPL", _valuation(symbol="AAPL", market=Market.US, price=50.0)),
            ),
            fx_rate=_fx(7.8),
        )
        # cash = 20,000 + 1,000 dividend = 21,000 HKD
        # securities = 1,000 x 10 + 200 x 50 x 7.8 = 10,000 + 78,000 = 88,000 HKD
        self.assertAlmostEqual(valuation.net_value.cash, 21_000.0, places=2)
        self.assertAlmostEqual(valuation.net_value.securities_value, 88_000.0, places=2)
        self.assertAlmostEqual(valuation.net_value.total_value, 109_000.0, places=2)

    def test_cash_action_and_consolidation_impact_net_value(self) -> None:
        # A HK tender offer adds cash; a consolidation halves the quantity but
        # keeps total market value at a doubled price.
        ledger = _ledger(base=HKD, initial=20_000.0)
        position = _position(symbol="0001.HK", market=Market.HK, quantity=1_000.0)
        ledger, position, _ = _apply_corporate_action(
            ledger, position, _event(action_type=_TENDER, price=12.0)
        )
        ledger, position, _ = _apply_corporate_action(
            ledger, position, _event(action_type=_CONSOLIDATION, ratio=0.5)
        )
        self.assertEqual(position.quantity, 500.0)

        valuation = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=(position,),
            valuations=_valuations(
                (Market.HK, "0001.HK", _valuation(symbol="0001.HK", price=20.0))
            ),
            fx_rate=_fx(None),
        )
        # cash = 20,000 + 12,000 = 32,000 HKD; securities = 500 x 20 = 10,000 HKD.
        self.assertAlmostEqual(valuation.net_value.cash, 32_000.0, places=2)
        self.assertAlmostEqual(valuation.net_value.securities_value, 10_000.0, places=2)
        self.assertAlmostEqual(valuation.net_value.total_value, 42_000.0, places=2)

    def test_readable_reports(self) -> None:
        ledger = _ledger(initial=10_000.0)
        position = _position(symbol="0001.HK", market=Market.HK, quantity=1_000.0)
        updated, payment = pay_dividend(
            ledger, dividend=_dividend(amount=1.0), quantity=position.quantity
        )
        assert payment is not None
        self.assertIn("dividend", payment.readable())
        valuation = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=updated,
            positions=(position,),
            valuations=_valuations(
                (Market.HK, "0001.HK", _valuation(symbol="0001.HK", price=10.0))
            ),
            fx_rate=_fx(None),
        )
        self.assertIsInstance(valuation, DailyValuation)
        self.assertIn("net value", valuation.readable())


if __name__ == "__main__":
    unittest.main()
