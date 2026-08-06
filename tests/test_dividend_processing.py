"""Dividend processing tests (MVP 2 / SP 2.43).

Verifies the entitlement date rule (record date with ex-date fallback), that
special dividends follow the strategy configuration, and that payments credit
the dividend's own currency ledger without an FX conversion.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import DividendConfig
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import Dividend
from harbor.core.dividend_processing import (
    CashDividend,
    entitlement_date,
    include_dividend,
    pay_dividend,
)
from harbor.core.ledger import Ledger, deposit, empty_ledger

HKD = Currency.HKD
USD = Currency.USD
_DAY = date(2024, 1, 2)
_EX = date(2024, 1, 5)
_RECORD = date(2024, 1, 8)
_PAY = date(2024, 1, 31)


def _dividend(
    *,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    amount: float = 1.0,
    currency: Currency = HKD,
    ex_date: date = _EX,
    record_date: date | None = _RECORD,
    payment_date: date | None = _PAY,
    is_special: bool = False,
) -> Dividend:
    return Dividend(
        market=market,
        symbol=symbol,
        amount=amount,
        currency=currency,
        ex_date=ex_date,
        record_date=record_date,
        payment_date=payment_date,
        is_special=is_special,
    )


def _ledger() -> Ledger:
    return empty_ledger(as_of=_DAY, base_currency=HKD)


class EntitlementDateTests(unittest.TestCase):
    """Verify the entitlement date rule."""

    def test_uses_record_date_when_present(self) -> None:
        self.assertEqual(entitlement_date(_dividend()), _RECORD)

    def test_falls_back_to_ex_date(self) -> None:
        self.assertEqual(entitlement_date(_dividend(record_date=None)), _EX)


class IncludeDividendTests(unittest.TestCase):
    """Verify special-dividend handling follows the configuration."""

    def test_regular_dividend_is_included_by_default(self) -> None:
        self.assertTrue(include_dividend(_dividend(is_special=False)))

    def test_special_dividend_is_included_by_default(self) -> None:
        self.assertTrue(include_dividend(_dividend(is_special=True)))

    def test_special_dividend_excluded_when_configured(self) -> None:
        config = DividendConfig(include_special=False)
        self.assertFalse(include_dividend(_dividend(is_special=True), config))
        self.assertTrue(include_dividend(_dividend(is_special=False), config))


class PayDividendTests(unittest.TestCase):
    """Verify payments credit the dividend's own currency."""

    def test_payment_credits_cash_in_dividend_currency(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        updated, payment = pay_dividend(ledger, dividend=_dividend(amount=2.0), quantity=100.0)
        self.assertIsInstance(payment, CashDividend)
        self.assertIsNotNone(payment)
        if payment is not None:
            self.assertAlmostEqual(payment.gross_amount, 200.0)
            self.assertEqual(payment.currency, HKD)
            self.assertEqual(payment.payment_date, _PAY)
        self.assertAlmostEqual(updated.balance(HKD), 1_200.0)

    def test_usd_dividend_credits_usd_not_hkd(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        updated, payment = pay_dividend(
            ledger,
            dividend=_dividend(
                symbol="AAPL",
                market=Market.US,
                currency=USD,
                amount=0.5,
            ),
            quantity=200.0,
        )
        self.assertIsNotNone(payment)
        if payment is not None:
            self.assertAlmostEqual(payment.gross_amount, 100.0)
        self.assertAlmostEqual(updated.balance(USD), 100.0)
        self.assertEqual(updated.balance(HKD), 0.0)  # no implicit conversion

    def test_zero_quantity_pays_nothing(self) -> None:
        ledger = _ledger()
        updated, payment = pay_dividend(ledger, dividend=_dividend(amount=2.0), quantity=0.0)
        self.assertIsNone(payment)
        self.assertIs(updated, ledger)

    def test_negative_quantity_is_rejected(self) -> None:
        ledger = _ledger()
        with self.assertRaisesRegex(ValueError, "quantity"):
            pay_dividend(ledger, dividend=_dividend(), quantity=-1.0)

    def test_excluded_special_dividend_pays_nothing(self) -> None:
        config = DividendConfig(include_special=False)
        ledger = _ledger()
        updated, payment = pay_dividend(
            ledger,
            dividend=_dividend(amount=2.0, is_special=True),
            quantity=100.0,
            config=config,
        )
        self.assertIsNone(payment)
        self.assertIs(updated, ledger)

    def test_captures_entitlement_date(self) -> None:
        ledger = _ledger()
        updated, payment = pay_dividend(
            ledger,
            dividend=_dividend(record_date=_RECORD, ex_date=_EX),
            quantity=100.0,
        )
        self.assertIsNotNone(payment)
        if payment is not None:
            self.assertEqual(payment.entitlement_date, _RECORD)

    def test_payment_is_immutable(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        pay_dividend(ledger, dividend=_dividend(amount=2.0), quantity=100.0)
        self.assertAlmostEqual(ledger.balance(HKD), 1_000.0)

    def test_readable_summary(self) -> None:
        ledger = _ledger()
        updated, payment = pay_dividend(ledger, dividend=_dividend(amount=2.0), quantity=100.0)
        self.assertIsNotNone(payment)
        if payment is not None:
            summary = payment.readable()
            self.assertIn("dividend 0001.HK (HKD)", summary)
            self.assertIn("100.00 shares x 2.0000 = 200.00", summary)
            self.assertIn("2024-01-31", summary)


if __name__ == "__main__":
    unittest.main()
