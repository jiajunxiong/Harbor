"""Portfolio net-value valuation tests (MVP 2 / SP 2.45).

Verifies that cash, position market values, cumulative fees and the base-currency
net value are computed per day, that foreign positions/cash are converted with
an explicit FX rate (missing FX refuses rather than assuming 1:1), and that a
carried-forward (missing-price) valuation is traceable.
"""

import unittest
from collections.abc import Callable
from datetime import date

from harbor.core.backtest_domain import CashBalance, Currency, Market, NetValue, Position
from harbor.core.fx import FxConversionError
from harbor.core.ledger import deposit, empty_ledger
from harbor.core.suspension import PositionValuation, SuspensionWarning
from harbor.core.valuation import DailyValuation, PositionValue, value_portfolio

HKD = Currency.HKD
USD = Currency.USD
_DAY = date(2024, 1, 2)


def _position(
    *,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    quantity: float = 100.0,
    currency: Currency | None = None,
) -> Position:
    ccy = currency if currency is not None else (HKD if market is Market.HK else USD)
    return Position(
        symbol=symbol,
        market=market,
        quantity=quantity,
        average_cost=10.0,
        currency=ccy,
        as_of_date=_DAY,
    )


def _valuation(
    *,
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    price: float = 10.0,
    carried_forward: bool = False,
) -> PositionValuation:
    return PositionValuation(
        market=market,
        symbol=symbol,
        price=price,
        carried_forward=carried_forward,
        day=_DAY,
        warning=(
            SuspensionWarning(market=market, symbol=symbol, day=_DAY, message="stale")
            if carried_forward
            else None
        ),
    )


def _fx(rate: float | None) -> Callable[[Currency, Currency, date], float | None]:
    def get(from_currency: Currency, to_currency: Currency, day: date) -> float | None:
        return rate

    return get


def _valuations(
    *items: tuple[Market, str, PositionValuation],
) -> dict[tuple[Market, str], PositionValuation]:
    return {(market, symbol): valuation for market, symbol, valuation in items}


class ValuationTests(unittest.TestCase):
    """Verify cash, positions, fees and net value in the base currency."""

    def test_hk_portfolio_net_value(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        ledger = deposit(ledger, currency=HKD, amount=400_000.0, base_rate=1.0)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[_position(quantity=1_000.0)],
            valuations=_valuations((Market.HK, "0001.HK", _valuation(price=60.0))),
            fx_rate=_fx(1.0),
        )
        self.assertIsInstance(result, DailyValuation)
        self.assertAlmostEqual(result.net_value.cash, 400_000.0)
        self.assertAlmostEqual(result.net_value.securities_value, 60_000.0)
        self.assertAlmostEqual(result.net_value.total_value, 460_000.0)
        self.assertEqual(result.net_value.currency, HKD)

    def test_us_position_converts_to_base(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[_position(symbol="AAPL", market=Market.US, quantity=100.0)],
            valuations=_valuations(
                (Market.US, "AAPL", _valuation(symbol="AAPL", market=Market.US, price=200.0))
            ),
            fx_rate=_fx(7.8),
        )
        self.assertAlmostEqual(result.net_value.securities_value, 156_000.0)

    def test_foreign_cash_converts_to_base(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=7.8)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[],
            valuations={},
            fx_rate=_fx(7.8),
        )
        self.assertAlmostEqual(result.net_value.cash, 7_800.0)

    def test_cumulative_fees_in_base(self) -> None:
        from harbor.core.backtest_domain import Fill, OrderSide
        from harbor.core.ledger import apply_fill

        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        ledger = deposit(ledger, currency=HKD, amount=1_000.0, base_rate=1.0)
        fill = Fill(
            order_ref="r",
            symbol="0001.HK",
            market=Market.HK,
            side=OrderSide.BUY,
            quantity=50.0,
            price=10.0,
            currency=HKD,
            trade_date=_DAY,
            fee=25.0,
        )
        ledger = apply_fill(ledger, fill=fill)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[],
            valuations={},
            fx_rate=_fx(1.0),
        )
        self.assertAlmostEqual(result.net_value.fees_paid, 25.0)

    def test_empty_portfolio_is_cash_only(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        ledger = deposit(ledger, currency=HKD, amount=100_000.0, base_rate=1.0)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[],
            valuations={},
            fx_rate=_fx(1.0),
        )
        self.assertEqual(result.position_values, ())
        self.assertAlmostEqual(result.net_value.total_value, 100_000.0)

    def test_fx_pnl_is_reported(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        ledger = deposit(ledger, currency=HKD, amount=1_000.0, base_rate=1.0)
        from harbor.core.ledger import convert

        ledger = convert(ledger, from_currency=HKD, to_currency=USD, amount=1_000.0, rate=0.128)
        ledger = convert(ledger, from_currency=USD, to_currency=HKD, amount=128.0, rate=7.9)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[],
            valuations={},
            fx_rate=_fx(1.0),
        )
        self.assertAlmostEqual(result.fx_pnl, 11.2)


class MissingPriceAndFxTests(unittest.TestCase):
    """Verify missing prices and FX are refused, not fabricated."""

    def test_missing_position_valuation_is_refused(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        with self.assertRaisesRegex(ValueError, "Missing valuation"):
            value_portfolio(
                as_of=_DAY,
                base_currency=HKD,
                ledger=ledger,
                positions=[_position()],
                valuations={},
                fx_rate=_fx(1.0),
            )

    def test_missing_fx_for_position_is_refused(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        with self.assertRaisesRegex(FxConversionError, "refusing to assume 1:1"):
            value_portfolio(
                as_of=_DAY,
                base_currency=HKD,
                ledger=ledger,
                positions=[_position(symbol="AAPL", market=Market.US, quantity=100.0)],
                valuations=_valuations(
                    (Market.US, "AAPL", _valuation(symbol="AAPL", market=Market.US, price=200.0))
                ),
                fx_rate=_fx(None),
            )

    def test_missing_fx_for_cash_is_refused(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        ledger = deposit(ledger, currency=USD, amount=1_000.0, base_rate=7.8)
        with self.assertRaisesRegex(FxConversionError, "refusing to assume 1:1"):
            value_portfolio(
                as_of=_DAY,
                base_currency=HKD,
                ledger=ledger,
                positions=[],
                valuations={},
                fx_rate=_fx(None),
            )

    def test_carried_forward_valuation_is_traceable(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        valuation = _valuation(price=10.0, carried_forward=True)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[_position(quantity=100.0)],
            valuations=_valuations((Market.HK, "0001.HK", valuation)),
            fx_rate=_fx(1.0),
        )
        position_value = result.position_values[0]
        self.assertTrue(position_value.carried_forward)
        self.assertIsNotNone(position_value.warning)
        self.assertIn("carried forward", position_value.readable())


class PositionValueTests(unittest.TestCase):
    """Verify per-position value metadata."""

    def test_position_value_metadata(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[_position(symbol="AAPL", market=Market.US, quantity=100.0)],
            valuations=_valuations(
                (Market.US, "AAPL", _valuation(symbol="AAPL", market=Market.US, price=200.0))
            ),
            fx_rate=_fx(7.8),
        )
        position_value = result.position_values[0]
        self.assertIsInstance(position_value, PositionValue)
        self.assertAlmostEqual(position_value.fx_rate, 7.8)
        self.assertAlmostEqual(position_value.market_value_quote, 20_000.0)
        self.assertAlmostEqual(position_value.market_value_base, 156_000.0)
        self.assertEqual(position_value.currency, USD)

    def test_net_value_is_domain_type(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        ledger = deposit(ledger, currency=HKD, amount=100.0, base_rate=1.0)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[],
            valuations={},
            fx_rate=_fx(1.0),
        )
        self.assertIsInstance(result.net_value, NetValue)
        self.assertAlmostEqual(result.net_value.cash, 100.0)
        self.assertEqual(result.cash, (CashBalance(currency=HKD, amount=100.0),))

    def test_readable_summary(self) -> None:
        ledger = empty_ledger(as_of=_DAY, base_currency=HKD)
        ledger = deposit(ledger, currency=HKD, amount=100_000.0, base_rate=1.0)
        result = value_portfolio(
            as_of=_DAY,
            base_currency=HKD,
            ledger=ledger,
            positions=[_position(quantity=100.0)],
            valuations=_valuations((Market.HK, "0001.HK", _valuation(price=10.0))),
            fx_rate=_fx(1.0),
        )
        summary = result.readable()
        self.assertIn("net value on 2024-01-02 (HKD)", summary)
        self.assertIn("securities: 1000.00", summary)
        self.assertIn("cumulative fees: 0.00", summary)


if __name__ == "__main__":
    unittest.main()
