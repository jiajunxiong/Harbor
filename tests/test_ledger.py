"""Multi-currency cash ledger tests (MVP 2 / SP 2.42).

Verifies that HKD / USD / base-currency cash are maintained separately, that
realized fees and FX translation P&L accumulate, that fills never perform an
implicit FX conversion, and that explicit conversions require a positive rate
and refuse to assume 1:1.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, Fill, Market, OrderSide
from harbor.core.fx import FxConversionError
from harbor.core.ledger import (
    AcquisitionRate,
    InsufficientCashError,
    Ledger,
    apply_fill,
    convert,
    credit,
    deposit,
    empty_ledger,
)

HKD = Currency.HKD
USD = Currency.USD
_BUY = OrderSide.BUY
_SELL = OrderSide.SELL
_DAY = date(2024, 1, 2)


def _ledger(*, base: Currency = HKD, as_of: date = _DAY) -> Ledger:
    return empty_ledger(as_of=as_of, base_currency=base)


def _fill(
    *,
    currency: Currency = HKD,
    side: OrderSide = _BUY,
    quantity: float = 100.0,
    price: float = 10.0,
    fee: float = 5.0,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
) -> Fill:
    return Fill(
        order_ref="r1",
        symbol=symbol,
        market=market,
        side=side,
        quantity=quantity,
        price=price,
        currency=currency,
        trade_date=_DAY,
        fee=fee,
    )


class EmptyAndDepositTests(unittest.TestCase):
    """Verify the empty ledger and deposits."""

    def test_empty_ledger(self) -> None:
        ledger = _ledger()
        self.assertEqual(ledger.balance(HKD), 0.0)
        self.assertEqual(ledger.fees(HKD), 0.0)
        self.assertEqual(ledger.fx_pnl, 0.0)
        self.assertEqual(ledger.currencies(), ())

    def test_deposit_base_currency(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000_000.0, base_rate=1.0)
        self.assertAlmostEqual(ledger.balance(HKD), 1_000_000.0)
        self.assertAlmostEqual(ledger.acquisition_rate(HKD), 1.0)

    def test_deposit_foreign_currency(self) -> None:
        ledger = deposit(_ledger(), currency=USD, amount=1_000.0, base_rate=7.8)
        self.assertAlmostEqual(ledger.balance(USD), 1_000.0)
        self.assertAlmostEqual(ledger.acquisition_rate(USD), 7.8)

    def test_deposit_updates_weighted_average_rate(self) -> None:
        ledger = _ledger()
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=7.0)
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=8.0)
        self.assertAlmostEqual(ledger.balance(USD), 2_000.0)
        self.assertAlmostEqual(ledger.acquisition_rate(USD), 7.5)

    def test_deposit_rejects_non_positive_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "amount"):
            deposit(_ledger(), currency=HKD, amount=0.0, base_rate=1.0)

    def test_deposit_rejects_non_positive_rate(self) -> None:
        with self.assertRaisesRegex(FxConversionError, "base rate"):
            deposit(_ledger(), currency=HKD, amount=100.0, base_rate=0.0)

    def test_deposit_is_immutable(self) -> None:
        original = _ledger()
        deposit(original, currency=HKD, amount=100.0, base_rate=1.0)
        self.assertEqual(original.balance(HKD), 0.0)


class ApplyFillTests(unittest.TestCase):
    """Verify fills move cash and accrue fees without FX."""

    def test_buy_reduces_cash_and_accrues_fee(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=10_000.0, base_rate=1.0)
        ledger = apply_fill(ledger, fill=_fill(side=_BUY, quantity=100.0, price=10.0, fee=5.0))
        self.assertAlmostEqual(ledger.balance(HKD), 10_000.0 - 1_005.0)
        self.assertAlmostEqual(ledger.fees(HKD), 5.0)

    def test_sell_adds_net_proceeds_and_accrues_fee(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        ledger = apply_fill(ledger, fill=_fill(side=_SELL, quantity=100.0, price=10.0, fee=5.0))
        self.assertAlmostEqual(ledger.balance(HKD), 1_000.0 + 995.0)
        self.assertAlmostEqual(ledger.fees(HKD), 5.0)

    def test_insufficient_cash_for_buy_is_refused(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=100.0, base_rate=1.0)
        with self.assertRaisesRegex(InsufficientCashError, "Insufficient HKD"):
            apply_fill(ledger, fill=_fill(side=_BUY, quantity=100.0, price=10.0, fee=5.0))

    def test_usd_fill_does_not_touch_hkd(self) -> None:
        ledger = _ledger()
        ledger = deposit(ledger, currency=USD, amount=2_000.0, base_rate=7.8)
        ledger = apply_fill(
            ledger,
            fill=_fill(
                currency=USD,
                side=_BUY,
                quantity=100.0,
                price=10.0,
                fee=2.0,
                symbol="AAPL",
                market=Market.US,
            ),
        )
        self.assertAlmostEqual(ledger.balance(USD), 2_000.0 - 1_002.0)
        self.assertEqual(ledger.balance(HKD), 0.0)  # no implicit conversion

    def test_fees_accumulate_across_fills(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=10_000.0, base_rate=1.0)
        ledger = apply_fill(ledger, fill=_fill(side=_BUY, quantity=100.0, price=10.0, fee=5.0))
        ledger = apply_fill(ledger, fill=_fill(side=_BUY, quantity=100.0, price=10.0, fee=5.0))
        self.assertAlmostEqual(ledger.fees(HKD), 10.0)

    def test_apply_fill_is_immutable(self) -> None:
        original = deposit(_ledger(), currency=HKD, amount=10_000.0, base_rate=1.0)
        apply_fill(original, fill=_fill(side=_BUY, quantity=100.0, price=10.0, fee=5.0))
        self.assertAlmostEqual(original.balance(HKD), 10_000.0)


class CreditTests(unittest.TestCase):
    """Verify cash credits that carry no explicit conversion rate (SP 2.43)."""

    def test_credit_adds_cash_without_touching_acquisition_rate(self) -> None:
        ledger = deposit(_ledger(), currency=USD, amount=1_000.0, base_rate=7.8)
        ledger = credit(ledger, currency=USD, amount=500.0)
        self.assertAlmostEqual(ledger.balance(USD), 1_500.0)
        self.assertAlmostEqual(ledger.acquisition_rate(USD), 7.8)

    def test_credit_base_currency(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        ledger = credit(ledger, currency=HKD, amount=200.0)
        self.assertAlmostEqual(ledger.balance(HKD), 1_200.0)

    def test_credit_rejects_non_positive_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "amount"):
            credit(_ledger(), currency=HKD, amount=0.0)

    def test_credit_is_immutable(self) -> None:
        original = _ledger()
        credit(original, currency=HKD, amount=100.0)
        self.assertEqual(original.balance(HKD), 0.0)


class ConvertTests(unittest.TestCase):
    """Verify explicit FX conversion and FX translation P&L."""

    def test_base_to_foreign_realizes_no_pnl(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        ledger = convert(ledger, from_currency=HKD, to_currency=USD, amount=1_000.0, rate=0.128)
        self.assertAlmostEqual(ledger.balance(HKD), 0.0)
        self.assertAlmostEqual(ledger.balance(USD), 128.0)
        self.assertEqual(ledger.fx_pnl, 0.0)

    def test_foreign_to_base_realizes_pnl(self) -> None:
        ledger = deposit(_ledger(), currency=USD, amount=1_000.0, base_rate=7.8)
        ledger = convert(ledger, from_currency=USD, to_currency=HKD, amount=1_000.0, rate=7.9)
        self.assertAlmostEqual(ledger.balance(USD), 0.0)
        self.assertAlmostEqual(ledger.balance(HKD), 7_900.0)
        self.assertAlmostEqual(ledger.fx_pnl, 100.0)  # 7900 - (1000 * 7.8)

    def test_foreign_to_base_realizes_loss(self) -> None:
        ledger = deposit(_ledger(), currency=USD, amount=1_000.0, base_rate=7.8)
        ledger = convert(ledger, from_currency=USD, to_currency=HKD, amount=1_000.0, rate=7.7)
        self.assertAlmostEqual(ledger.fx_pnl, -100.0)

    def test_round_trip_at_same_rate_realizes_no_pnl(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        ledger = convert(ledger, from_currency=HKD, to_currency=USD, amount=1_000.0, rate=0.128)
        ledger = convert(ledger, from_currency=USD, to_currency=HKD, amount=128.0, rate=7.8125)
        self.assertAlmostEqual(ledger.balance(HKD), 1_000.0)
        self.assertAlmostEqual(ledger.balance(USD), 0.0)
        self.assertAlmostEqual(ledger.fx_pnl, 0.0)

    def test_round_trip_with_shift_realizes_pnl(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        ledger = convert(ledger, from_currency=HKD, to_currency=USD, amount=1_000.0, rate=0.128)
        ledger = convert(ledger, from_currency=USD, to_currency=HKD, amount=128.0, rate=7.9)
        self.assertAlmostEqual(ledger.fx_pnl, 11.2)  # 128 * (7.9 - 7.8125)

    def test_conversion_sets_acquisition_rate(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        ledger = convert(ledger, from_currency=HKD, to_currency=USD, amount=1_000.0, rate=0.128)
        self.assertAlmostEqual(ledger.acquisition_rate(USD), 1.0 / 0.128)

    def test_convert_same_currency_is_refused(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        with self.assertRaisesRegex(FxConversionError, "distinct currencies"):
            convert(ledger, from_currency=HKD, to_currency=HKD, amount=100.0, rate=1.0)

    def test_convert_non_positive_rate_is_refused(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        with self.assertRaisesRegex(FxConversionError, "rate"):
            convert(ledger, from_currency=HKD, to_currency=USD, amount=100.0, rate=0.0)

    def test_convert_insufficient_cash_is_refused(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=100.0, base_rate=1.0)
        with self.assertRaisesRegex(InsufficientCashError, "Insufficient HKD"):
            convert(ledger, from_currency=HKD, to_currency=USD, amount=200.0, rate=0.128)

    def test_convert_is_immutable(self) -> None:
        original = deposit(_ledger(), currency=HKD, amount=1_000.0, base_rate=1.0)
        convert(original, from_currency=HKD, to_currency=USD, amount=1_000.0, rate=0.128)
        self.assertAlmostEqual(original.balance(HKD), 1_000.0)
        self.assertEqual(original.balance(USD), 0.0)


class ReadableTests(unittest.TestCase):
    """Verify the human-readable summary."""

    def test_readable_summary(self) -> None:
        ledger = deposit(_ledger(), currency=HKD, amount=10_000.0, base_rate=1.0)
        ledger = apply_fill(ledger, fill=_fill(side=_BUY, quantity=100.0, price=10.0, fee=5.0))
        summary = ledger.readable()
        self.assertIn("base HKD", summary)
        self.assertIn("cash HKD", summary)
        self.assertIn("realized fees HKD: 5.00", summary)
        self.assertIn("fx pnl (base): 0.00", summary)

    def test_empty_ledger_readable(self) -> None:
        summary = _ledger().readable()
        self.assertIn("cash: none", summary)


class LedgerValidationTests(unittest.TestCase):
    """Verify the immutable record validation."""

    def test_acquisition_rate_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            AcquisitionRate(currency=HKD, rate=0.0)

    def test_duplicate_cash_currency_is_rejected(self) -> None:
        from harbor.core.backtest_domain import CashBalance

        with self.assertRaisesRegex(ValueError, "duplicate currencies"):
            Ledger(
                as_of=_DAY,
                base_currency=HKD,
                cash=(
                    CashBalance(currency=HKD, amount=1.0),
                    CashBalance(currency=HKD, amount=2.0),
                ),
            )

    def test_missing_currency_balance_is_zero(self) -> None:
        self.assertEqual(_ledger().balance(USD), 0.0)
        self.assertEqual(_ledger().acquisition_rate(USD), 0.0)


if __name__ == "__main__":
    unittest.main()
