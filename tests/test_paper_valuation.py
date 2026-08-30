"""Paper valuation tests (MVP 4 / SP 4.56).

Verifies daily cash / position market value / fees / base net value, the
missing-price carry-forward rule with warnings, and FX refusal for missing
rates (never 1:1).
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.paper_account import apply_paper_fill, convert_paper_cash, open_paper_account
from harbor.core.paper_domain import PaperFill
from harbor.core.paper_valuation import (
    PaperValuation,
    PaperValuationError,
    value_paper_account,
)

_TRADE = date(2026, 1, 2)
_PREV = date(2026, 1, 1)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _open():
    return open_paper_account(
        account_id="acc-1",
        base_currency=Currency.HKD,
        currencies=(Currency.HKD, Currency.USD),
        initial_capital=1_000_000.0,
        opened_at=_PREV,
    )


def _usd_open():
    state = _open()
    return convert_paper_cash(
        state, from_currency=Currency.HKD, to_currency=Currency.USD, amount=200_000.0, rate=0.128
    )


def _buy(
    symbol: str, market: Market, currency: Currency, quantity: float, price: float
) -> PaperFill:
    return PaperFill(
        fill_id=f"fill-{symbol}",
        paper_order_id=f"order-{symbol}",
        paper_run_id="paper-1",
        market=market,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        currency=currency,
        trade_date=_TRADE,
        fee=5.0,
    )


def _quote(close: float, day: date, volume: int = 100_000) -> DailyQuote:
    return DailyQuote(
        market=Market.HK,
        symbol="0001.HK",
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _fx_none(_from, _to, _as_of):
    return None


class PaperValuationTests(unittest.TestCase):
    """The daily valuation (SP 4.56)."""

    def test_same_currency_valuation_closes(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_buy("0001.HK", Market.HK, Currency.HKD, 100.0, 50.0))
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes={(Market.HK, "0001.HK"): _quote(55.0, _TRADE)},
            fx_rate=_fx_none,
        )
        self.assertIsInstance(valuation, PaperValuation)
        self.assertAlmostEqual(valuation.cash_base, 1_000_000.0 - 5_005.0, places=6)
        self.assertAlmostEqual(valuation.securities_base, 5_500.0, places=6)
        self.assertAlmostEqual(valuation.total_base, 1_000_495.0, places=6)
        self.assertTrue(valuation.reconciled())
        self.assertEqual(valuation.missing_prices, ())

    def test_position_value_details(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_buy("0001.HK", Market.HK, Currency.HKD, 100.0, 50.0))
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes={(Market.HK, "0001.HK"): _quote(55.0, _TRADE)},
            fx_rate=_fx_none,
        )
        position = valuation.position_values[0]
        self.assertEqual(position.market_value_quote, 5_500.0)
        self.assertEqual(position.market_value_base, 5_500.0)
        self.assertEqual(position.fx_rate, 1.0)
        self.assertFalse(position.carried_forward)

    def test_cross_currency_requires_fx(self) -> None:
        state = _usd_open()
        state = apply_paper_fill(state, fill=_buy("AAPL", Market.US, Currency.USD, 10.0, 100.0))
        with self.assertRaises(PaperValuationError) as ctx:
            value_paper_account(
                account=state,
                as_of=_TRADE,
                quotes={(Market.US, "AAPL"): _quote(100.0, _TRADE)},
                fx_rate=_fx_none,
            )
        self.assertIn("refusing to assume 1:1", str(ctx.exception))

    def test_cross_currency_with_fx(self) -> None:
        state = _usd_open()
        state = apply_paper_fill(state, fill=_buy("AAPL", Market.US, Currency.USD, 10.0, 100.0))
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes={(Market.US, "AAPL"): _quote(100.0, _TRADE)},
            fx_rate=lambda _from, _to, _as_of: 7.8,
        )
        self.assertAlmostEqual(valuation.securities_base, 7_800.0, places=6)

    def test_carried_forward_with_warning(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_buy("0001.HK", Market.HK, Currency.HKD, 100.0, 50.0))
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes={(Market.HK, "0001.HK"): None},
            fx_rate=_fx_none,
            last_quotes={(Market.HK, "0001.HK"): _quote(50.0, _PREV)},
        )
        position = valuation.position_values[0]
        self.assertTrue(position.carried_forward)
        self.assertIsNotNone(position.warning)
        self.assertEqual(position.price, 50.0)

    def test_no_price_records_missing(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_buy("0001.HK", Market.HK, Currency.HKD, 100.0, 50.0))
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes={(Market.HK, "0001.HK"): None},
            fx_rate=_fx_none,
        )
        self.assertEqual(len(valuation.missing_prices), 1)
        self.assertEqual(valuation.missing_prices[0].rule, "no_price")
        self.assertEqual(valuation.position_values, ())
        self.assertIn("missing price", valuation.readable())

    def test_readable(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_buy("0001.HK", Market.HK, Currency.HKD, 100.0, 50.0))
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes={(Market.HK, "0001.HK"): _quote(55.0, _TRADE)},
            fx_rate=_fx_none,
        )
        self.assertIn("paper valuation", valuation.readable())
