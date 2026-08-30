"""Paper account model tests (MVP 4 / SP 4.4).

Verifies the multi-currency account: funding in the base currency, fills that
move cash/fees in the fill currency and update the weighted-average cost
basis, explicit-only FX conversion (no implicit 1:1) and base-currency asset
aggregation that refuses a missing FX rate.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.fx import FxConversionError
from harbor.core.ledger import InsufficientCashError
from harbor.core.paper_account import (
    PaperAccountError,
    account_assets_base,
    apply_paper_fill,
    convert_paper_cash,
    open_paper_account,
)
from harbor.core.paper_domain import PaperFill

_OPENED = date(2026, 1, 1)
_TRADE = date(2026, 1, 2)
_USD_TO_HKD = 7.8125  # exact inverse of the HKD->USD funding rate 0.128


def _open(**overrides: object):
    """Return a funded HKD/USD account with overridable kwargs."""
    fields: dict[str, object] = {
        "account_id": "acc-1",
        "base_currency": Currency.HKD,
        "currencies": (Currency.HKD, Currency.USD),
        "initial_capital": 1_000_000.0,
        "opened_at": _OPENED,
    }
    fields.update(overrides)
    return open_paper_account(**fields)  # type: ignore[arg-type]


def _usd_funded():
    """Return an account with 25600 USD funded from 200k HKD at 0.128."""
    state = _open()
    return convert_paper_cash(
        state,
        from_currency=Currency.HKD,
        to_currency=Currency.USD,
        amount=200_000.0,
        rate=0.128,
    )


def _fill(**overrides: object) -> PaperFill:
    """Return a valid paper fill with overridable fields."""
    fields: dict[str, object] = {
        "fill_id": "fill-1",
        "paper_order_id": "order-1",
        "paper_run_id": "paper-1",
        "market": Market.HK,
        "symbol": "0001.HK",
        "side": OrderSide.BUY,
        "quantity": 100.0,
        "price": 50.0,
        "currency": Currency.HKD,
        "trade_date": _TRADE,
        "fee": 5.0,
    }
    fields.update(overrides)
    return PaperFill(**fields)  # type: ignore[arg-type]


def _fx(rate: float | None):
    """Return a fixed FX accessor (currency -> base)."""
    return lambda _from, _to: rate


def _usd_fx():
    """Return the USD->HKD explicit FX accessor."""
    return lambda _from, _to: _USD_TO_HKD


class OpenAccountTests(unittest.TestCase):
    """Account funding (SP 4.4)."""

    def test_open_funds_base_currency(self) -> None:
        state = _open()
        self.assertEqual(state.balance(Currency.HKD), 1_000_000.0)
        self.assertEqual(state.balance(Currency.USD), 0.0)
        self.assertEqual(state.positions, ())
        self.assertEqual(state.account.initial_capital, 1_000_000.0)

    def test_open_rejects_invalid_account(self) -> None:
        with self.assertRaises(ValueError):
            _open(base_currency=Currency.USD, currencies=(Currency.HKD,))
        with self.assertRaises(ValueError):
            _open(initial_capital=-1.0)


class ApplyFillTests(unittest.TestCase):
    """Fills update cash, fees and positions (SP 4.4)."""

    def test_buy_moves_cash_and_opens_position(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill())
        self.assertEqual(state.balance(Currency.HKD), 1_000_000.0 - 5_005.0)
        self.assertEqual(state.fees(Currency.HKD), 5.0)
        position = state.position(Market.HK, "0001.HK")
        self.assertIsNotNone(position)
        self.assertEqual(position.quantity, 100.0)
        self.assertEqual(position.average_cost, 50.0)

    def test_buy_updates_weighted_average_cost(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill(fill_id="f1", quantity=100.0, price=50.0))
        state = apply_paper_fill(state, fill=_fill(fill_id="f2", quantity=100.0, price=60.0))
        position = state.position(Market.HK, "0001.HK")
        self.assertEqual(position.quantity, 200.0)
        self.assertEqual(position.average_cost, 55.0)
        self.assertEqual(position.cost_basis, 11_000.0)

    def test_sell_reduces_quantity_keeps_average_cost(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill(quantity=100.0, price=50.0))
        state = apply_paper_fill(
            state, fill=_fill(fill_id="f2", side=OrderSide.SELL, quantity=40.0, price=55.0)
        )
        position = state.position(Market.HK, "0001.HK")
        self.assertEqual(position.quantity, 60.0)
        self.assertEqual(position.average_cost, 50.0)
        # sell proceeds (40 * 55 - fee) return to HKD cash
        self.assertEqual(state.balance(Currency.HKD), 1_000_000.0 - 5_005.0 + 2_195.0)

    def test_sell_to_zero_removes_position(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill(quantity=100.0, price=50.0))
        state = apply_paper_fill(
            state, fill=_fill(fill_id="f2", side=OrderSide.SELL, quantity=100.0, price=50.0)
        )
        self.assertIsNone(state.position(Market.HK, "0001.HK"))

    def test_sell_without_position_rejected(self) -> None:
        state = _open()
        with self.assertRaises(PaperAccountError):
            apply_paper_fill(
                state,
                fill=_fill(side=OrderSide.SELL, quantity=10.0, price=50.0),
            )

    def test_sell_more_than_held_rejected(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill(quantity=100.0, price=50.0))
        with self.assertRaises(PaperAccountError):
            apply_paper_fill(
                state,
                fill=_fill(fill_id="f2", side=OrderSide.SELL, quantity=101.0, price=50.0),
            )

    def test_insufficient_cash_rejected(self) -> None:
        state = _open()
        with self.assertRaises(InsufficientCashError):
            apply_paper_fill(state, fill=_fill(fill_id="f2", quantity=30_000.0, price=50.0))

    def test_currency_mismatch_rejected(self) -> None:
        state = _usd_funded()
        state = apply_paper_fill(
            state,
            fill=_fill(
                fill_id="f1",
                market=Market.US,
                symbol="AAPL",
                currency=Currency.USD,
                quantity=10.0,
                price=100.0,
                fee=1.0,
            ),
        )
        with self.assertRaises(PaperAccountError):
            apply_paper_fill(
                state,
                fill=_fill(
                    fill_id="f2",
                    market=Market.US,
                    symbol="AAPL",
                    currency=Currency.HKD,
                    quantity=10.0,
                    price=100.0,
                ),
            )

    def test_usd_fill_keeps_usd_cash_separate(self) -> None:
        state = _usd_funded()
        state = apply_paper_fill(
            state,
            fill=_fill(
                fill_id="f2",
                market=Market.US,
                symbol="AAPL",
                currency=Currency.USD,
                quantity=10.0,
                price=100.0,
                fee=1.0,
            ),
        )
        self.assertEqual(state.balance(Currency.USD), 25_600.0 - 1_001.0)
        self.assertEqual(state.balance(Currency.HKD), 800_000.0)
        position = state.position(Market.US, "AAPL")
        self.assertEqual(position.average_cost, 100.0)


class ConvertCashTests(unittest.TestCase):
    """Explicit-only FX conversion (SP 4.4)."""

    def test_convert_requires_positive_rate(self) -> None:
        state = _open()
        with self.assertRaises(FxConversionError):
            convert_paper_cash(
                state,
                from_currency=Currency.HKD,
                to_currency=Currency.USD,
                amount=100.0,
                rate=0.0,
            )
        with self.assertRaises(FxConversionError):
            convert_paper_cash(
                state,
                from_currency=Currency.HKD,
                to_currency=Currency.USD,
                amount=100.0,
                rate=-0.1,
            )

    def test_convert_same_currency_rejected(self) -> None:
        state = _open()
        with self.assertRaises(FxConversionError):
            convert_paper_cash(
                state,
                from_currency=Currency.HKD,
                to_currency=Currency.HKD,
                amount=100.0,
                rate=1.0,
            )

    def test_convert_base_to_foreign(self) -> None:
        state = _open()
        state = convert_paper_cash(
            state,
            from_currency=Currency.HKD,
            to_currency=Currency.USD,
            amount=100_000.0,
            rate=0.128,
        )
        self.assertAlmostEqual(state.balance(Currency.USD), 12_800.0, places=4)
        self.assertAlmostEqual(state.balance(Currency.HKD), 900_000.0, places=4)

    def test_convert_insufficient_cash_rejected(self) -> None:
        state = _open()
        with self.assertRaises(InsufficientCashError):
            convert_paper_cash(
                state,
                from_currency=Currency.USD,
                to_currency=Currency.HKD,
                amount=1_000_000.0,
                rate=7.8,
            )


class AccountAssetsTests(unittest.TestCase):
    """Base-currency aggregation with explicit FX (SP 4.4)."""

    def test_hkd_only_account_closes(self) -> None:
        state = _open()
        assets = account_assets_base(state, fx_rate=_fx(None))
        self.assertTrue(assets.reconciled())
        self.assertAlmostEqual(assets.total_base, 1_000_000.0, places=6)

    def test_after_buy_cost_basis_closes(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill())
        assets = account_assets_base(state, fx_rate=_fx(None))
        self.assertTrue(assets.reconciled())
        self.assertAlmostEqual(assets.total_base, 1_000_000.0 - 5.0, places=6)

    def test_foreign_position_requires_fx(self) -> None:
        state = _usd_funded()
        state = apply_paper_fill(
            state,
            fill=_fill(
                fill_id="f2",
                market=Market.US,
                symbol="AAPL",
                currency=Currency.USD,
                quantity=10.0,
                price=100.0,
                fee=1.0,
            ),
        )
        with self.assertRaises(PaperAccountError) as ctx:
            account_assets_base(state, fx_rate=_fx(None))
        self.assertIn("refusing to assume 1:1", str(ctx.exception))

    def test_foreign_position_converts_with_explicit_rate(self) -> None:
        state = _usd_funded()
        state = apply_paper_fill(
            state,
            fill=_fill(
                fill_id="f2",
                market=Market.US,
                symbol="AAPL",
                currency=Currency.USD,
                quantity=10.0,
                price=100.0,
                fee=1.0,
            ),
        )
        assets = account_assets_base(state, fx_rate=_usd_fx())
        self.assertTrue(assets.reconciled())
        # conversion is value-neutral (inverse rates); only the USD fee reduces total
        self.assertAlmostEqual(assets.total_base, 1_000_000.0 - 1.0 * _USD_TO_HKD, places=4)

    def test_non_positive_rate_refused(self) -> None:
        state = _usd_funded()
        state = apply_paper_fill(
            state,
            fill=_fill(
                fill_id="f2",
                market=Market.US,
                symbol="AAPL",
                currency=Currency.USD,
                quantity=10.0,
                price=100.0,
                fee=1.0,
            ),
        )
        with self.assertRaises(PaperAccountError):
            account_assets_base(state, fx_rate=_fx(0.0))
