"""Exposure and concentration metrics tests (MVP 2 / SP 2.55).

Verifies the per-day market, currency, individual-stock, cash and optional
industry exposures as fractions of total net value, and that a non-positive
total value or a missing foreign-cash FX rate raises :class:`ExposureError`
rather than fabricating a value.
"""

import unittest
from collections.abc import Callable
from datetime import date

from harbor.core.backtest_domain import CashBalance, Currency, Market, NetValue
from harbor.core.exposure import (
    ExposureError,
    ExposurePoint,
    ExposureSeries,
    compute_exposure_series,
)
from harbor.core.valuation import DailyValuation, PositionValue

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_DAY = date(2024, 1, 2)
_DAY2 = date(2024, 1, 3)


def _position_value(
    *,
    market: Market = HK,
    symbol: str = "0001.HK",
    quantity: float = 100.0,
    price: float = 50.0,
    currency: Currency = HKD,
    fx_rate: float = 1.0,
) -> PositionValue:
    return PositionValue(
        market=market,
        symbol=symbol,
        quantity=quantity,
        price=price,
        currency=currency,
        fx_rate=fx_rate,
        market_value_quote=quantity * price,
        market_value_base=quantity * price * fx_rate,
        carried_forward=False,
        warning=None,
    )


def _valuation(
    *,
    as_of: date = _DAY,
    base: Currency = HKD,
    cash: tuple[CashBalance, ...] = (),
    positions: tuple[PositionValue, ...] = (),
    fees_paid: float = 0.0,
) -> DailyValuation:
    cash_base = sum(balance.amount for balance in cash if balance.currency is base) + sum(
        balance.amount * 7.8 for balance in cash if balance.currency is not base
    )
    securities = sum(position.market_value_base for position in positions)
    return DailyValuation(
        as_of=as_of,
        base_currency=base,
        cash=cash,
        position_values=positions,
        realized_fees=(),
        fx_pnl=0.0,
        net_value=NetValue(
            as_of_date=as_of,
            currency=base,
            cash=cash_base,
            securities_value=securities,
            fees_paid=fees_paid,
        ),
    )


def _fx(rate: float | None) -> Callable[[Currency, Currency, date], float | None]:
    def get(from_currency: Currency, to_currency: Currency, day: date) -> float | None:
        return rate

    return get


def _industry(market: Market, symbol: str) -> str | None:
    mapping = {"0001.HK": "Financials", "AAPL": "Technology", "0002.HK": "Utilities"}
    return mapping.get(symbol)


class HkExposureTests(unittest.TestCase):
    """Verify HK-only exposures (SP 2.55)."""

    def setUp(self) -> None:
        self.valuation = _valuation(
            cash=(CashBalance(currency=HKD, amount=20_000.0),),
            positions=(
                _position_value(symbol="0001.HK", quantity=100.0, price=50.0),
                _position_value(symbol="0002.HK", quantity=50.0, price=40.0),
            ),
        )

    def test_cash_and_symbol_exposures(self) -> None:
        point = compute_exposure_series((self.valuation,), fx_rate=_fx(None)).points[0]
        self.assertIsInstance(point, ExposurePoint)
        # total = 20,000 cash + 5,000 + 2,000 = 27,000
        self.assertAlmostEqual(point.total_value, 27_000.0, places=2)
        self.assertAlmostEqual(point.cash_exposure, 20_000.0 / 27_000.0, places=6)
        self.assertAlmostEqual(point.symbol_exposure[(HK, "0001.HK")], 5_000.0 / 27_000.0, places=6)
        self.assertAlmostEqual(point.symbol_exposure[(HK, "0002.HK")], 2_000.0 / 27_000.0, places=6)

    def test_market_and_currency_exposures_sum_to_one(self) -> None:
        point = compute_exposure_series((self.valuation,), fx_rate=_fx(None)).points[0]
        self.assertAlmostEqual(point.market_exposure[HK], 7_000.0 / 27_000.0, places=6)
        self.assertAlmostEqual(point.currency_exposure[HKD], 1.0, places=6)
        # cash + market == 1
        self.assertAlmostEqual(point.cash_exposure + point.market_exposure[HK], 1.0, places=6)

    def test_series_helpers(self) -> None:
        series = compute_exposure_series((self.valuation,), fx_rate=_fx(None))
        self.assertIsInstance(series, ExposureSeries)
        self.assertEqual(series.cash_series()[0][0], _DAY)
        self.assertGreater(series.cash_series()[0][1], 0.0)
        self.assertEqual(len(series.market_series(HK)), 1)
        self.assertEqual(len(series.symbol_series(HK, "0001.HK")), 1)

    def test_readable(self) -> None:
        series = compute_exposure_series((self.valuation,), fx_rate=_fx(None))
        self.assertIn("cash", series.readable())


class IndustryExposureTests(unittest.TestCase):
    """Verify optional industry exposure (行业暴露)."""

    def test_industry_exposure_when_classifier_supplied(self) -> None:
        valuation = _valuation(
            cash=(CashBalance(currency=HKD, amount=10_000.0),),
            positions=(
                _position_value(symbol="0001.HK", quantity=100.0, price=50.0),
                _position_value(symbol="0002.HK", quantity=50.0, price=40.0),
            ),
        )
        point = compute_exposure_series((valuation,), fx_rate=_fx(None), industry=_industry).points[
            0
        ]
        # total = 10,000 + 5,000 + 2,000 = 17,000
        self.assertIsNotNone(point.industry_exposure)
        assert point.industry_exposure is not None
        self.assertAlmostEqual(point.industry_exposure["Financials"], 5_000.0 / 17_000.0, places=6)
        self.assertAlmostEqual(point.industry_exposure["Utilities"], 2_000.0 / 17_000.0, places=6)

    def test_industry_exposure_none_without_classifier(self) -> None:
        valuation = _valuation(
            positions=(_position_value(symbol="0001.HK", quantity=100.0, price=50.0),),
        )
        point = compute_exposure_series((valuation,), fx_rate=_fx(None)).points[0]
        self.assertIsNone(point.industry_exposure)

    def test_industry_series(self) -> None:
        valuation = _valuation(
            positions=(
                _position_value(symbol="AAPL", market=US, quantity=10.0, price=100.0, fx_rate=7.8),
            ),
        )
        series = compute_exposure_series((valuation,), fx_rate=_fx(None), industry=_industry)
        # AAPL market_value_base = 10 * 100 * 7.8 = 7,800; no cash.
        self.assertAlmostEqual(series.industry_series("Technology")[0][1], 1.0, places=6)


class CrossMarketExposureTests(unittest.TestCase):
    """Verify cross-market exposures convert cash via FX (SP 2.55/2.12)."""

    def test_cross_market_currency_exposures(self) -> None:
        valuation = _valuation(
            cash=(CashBalance(currency=HKD, amount=2_200.0),),
            positions=(
                _position_value(
                    symbol="AAPL", market=US, quantity=10.0, price=100.0, currency=USD, fx_rate=7.8
                ),
            ),
        )
        point = compute_exposure_series((valuation,), fx_rate=_fx(None)).points[0]
        # AAPL base = 7,800; total = 10,000.
        self.assertAlmostEqual(point.currency_exposure[USD], 7_800.0 / 10_000.0, places=6)
        self.assertAlmostEqual(point.currency_exposure[HKD], 2_200.0 / 10_000.0, places=6)
        self.assertAlmostEqual(point.market_exposure[US], 0.78, places=6)

    def test_foreign_cash_requires_fx(self) -> None:
        valuation = _valuation(
            cash=(CashBalance(currency=USD, amount=100.0),),
            positions=(),
        )
        with self.assertRaisesRegex(ExposureError, "refusing to assume 1:1"):
            compute_exposure_series((valuation,), fx_rate=_fx(None))

    def test_foreign_cash_converted_at_rate(self) -> None:
        valuation = _valuation(
            cash=(CashBalance(currency=USD, amount=100.0),),
            positions=(),
        )
        point = compute_exposure_series((valuation,), fx_rate=_fx(7.8)).points[0]
        self.assertAlmostEqual(point.cash_exposure, 1.0, places=6)
        self.assertAlmostEqual(point.total_value, 780.0, places=2)


class BoundaryTests(unittest.TestCase):
    """Verify refusal on non-positive totals and multi-day series."""

    def test_nonpositive_total_is_refused(self) -> None:
        valuation = _valuation(cash=(CashBalance(currency=HKD, amount=0.0),), positions=())
        with self.assertRaisesRegex(ExposureError, "not positive"):
            compute_exposure_series((valuation,), fx_rate=_fx(None))

    def test_multi_day_series(self) -> None:
        first = _valuation(
            as_of=_DAY,
            positions=(_position_value(symbol="0001.HK", quantity=100.0, price=50.0),),
        )
        second = _valuation(
            as_of=_DAY2,
            positions=(_position_value(symbol="0001.HK", quantity=100.0, price=60.0),),
        )
        series = compute_exposure_series((first, second), fx_rate=_fx(None))
        self.assertEqual(len(series.points), 2)
        self.assertEqual(series.cash_series()[1][0], _DAY2)

    def test_empty_series_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one day"):
            ExposureSeries(points=())


if __name__ == "__main__":
    unittest.main()
