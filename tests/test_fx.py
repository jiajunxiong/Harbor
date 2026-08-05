"""Currency conversion tests (MVP 2 / SP 2.12)."""

import unittest

from harbor.core.backtest_domain import Currency
from harbor.core.fx import FxConversionError, convert, convert_to_base


class ConvertTests(unittest.TestCase):
    """Verify raw FX conversion."""

    def test_convert_multiplies_by_rate(self) -> None:
        self.assertEqual(convert(1_000.0, 0.128), 128.0)

    def test_convert_rejects_non_positive_rate(self) -> None:
        with self.assertRaisesRegex(FxConversionError, "must be positive"):
            convert(1_000.0, 0.0)
        with self.assertRaisesRegex(FxConversionError, "must be positive"):
            convert(1_000.0, -1.0)


class ConvertToBaseTests(unittest.TestCase):
    """Verify conversion into the benchmark currency refuses 1:1 shortcuts."""

    def test_same_currency_returns_amount_unchanged(self) -> None:
        amount = convert_to_base(1_000.0, Currency.HKD, Currency.HKD, rate=None)
        self.assertEqual(amount, 1_000.0)

    def test_different_currency_converts_at_rate(self) -> None:
        amount = convert_to_base(1_000.0, Currency.HKD, Currency.USD, rate=0.128)
        self.assertEqual(amount, 128.0)

    def test_missing_rate_is_refused_not_assumed_one_to_one(self) -> None:
        with self.assertRaisesRegex(FxConversionError, "refusing to assume 1:1"):
            convert_to_base(1_000.0, Currency.HKD, Currency.USD, rate=None)

    def test_non_positive_rate_is_refused(self) -> None:
        with self.assertRaisesRegex(FxConversionError, "must be positive"):
            convert_to_base(1_000.0, Currency.HKD, Currency.USD, rate=0.0)


if __name__ == "__main__":
    unittest.main()
