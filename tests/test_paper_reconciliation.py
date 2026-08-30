"""Paper reconciliation tests (MVP 4 / SP 4.57-4.58 / 4.61).

Verifies order/fill/account consistency, daily asset closure, and that every
difference is recorded and mapped to table rows (never silently corrected).
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import CashBalance, Currency, Market, NetValue, OrderSide
from harbor.core.paper_account import apply_paper_fill, open_paper_account
from harbor.core.paper_domain import (
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
)
from harbor.core.paper_reconciliation import (
    PaperReconciliationDifference,
    PaperReconciliationResult,
    reconcile_daily_assets,
    reconcile_order_fill_account,
    reconciliation_rows,
)
from harbor.core.paper_valuation import PaperValuation

_TRADE = date(2026, 1, 2)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _open():
    return open_paper_account(
        account_id="acc-1",
        base_currency=Currency.HKD,
        currencies=(Currency.HKD,),
        initial_capital=1_000_000.0,
        opened_at=date(2026, 1, 1),
    )


def _order() -> PaperOrder:
    return PaperOrder(
        order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=OrderSide.BUY,
        quantity=100.0,
        currency=Currency.HKD,
        price_type=PaperPriceType.REFERENCE,
        status=PaperOrderStatus.CREATED,
        created_at=_utc_at(2026, 1, 2, 9),
        intention_id="sig-1",
    )


def _fill(quantity: float = 100.0, price: float = 50.0, fee: float = 5.0) -> PaperFill:
    return PaperFill(
        fill_id="fill-1",
        paper_order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        currency=Currency.HKD,
        trade_date=_TRADE,
        fee=fee,
    )


class OrderFillAccountTests(unittest.TestCase):
    """The order/fill/account consistency check (SP 4.57)."""

    def test_consistent_fill_reconciles(self) -> None:
        before = _open()
        fill = _fill()
        after = apply_paper_fill(before, fill=fill)
        result = reconcile_order_fill_account(
            order=_order(), fill=fill, account_before=before, account_after=after
        )
        self.assertIsInstance(result, PaperReconciliationResult)
        self.assertTrue(result.reconciled)
        self.assertEqual(result.differences, ())

    def test_cash_mismatch_located(self) -> None:
        before = _open()
        # apply a fill of 100 shares, but check with a fill of 50 shares
        applied = apply_paper_fill(before, fill=_fill(quantity=100.0))
        checked = _fill(quantity=50.0)
        result = reconcile_order_fill_account(
            order=_order(), fill=checked, account_before=before, account_after=applied
        )
        self.assertFalse(result.reconciled)
        names = {difference.check_name for difference in result.differences}
        self.assertIn("cash_delta", names)
        self.assertIn("position_quantity", names)

    def test_fee_mismatch_located(self) -> None:
        before = _open()
        after = apply_paper_fill(before, fill=_fill(fee=10.0))
        checked = _fill(fee=5.0)
        result = reconcile_order_fill_account(
            order=_order(), fill=checked, account_before=before, account_after=after
        )
        self.assertFalse(result.reconciled)
        names = {difference.check_name for difference in result.differences}
        self.assertIn("fees_accrual", names)

    def test_readable(self) -> None:
        before = _open()
        after = apply_paper_fill(before, fill=_fill(fee=10.0))
        checked = _fill(fee=5.0)
        result = reconcile_order_fill_account(
            order=_order(), fill=checked, account_before=before, account_after=after
        )
        self.assertIn("MISMATCH", result.readable())


class DailyAssetsTests(unittest.TestCase):
    """The daily asset closure check (SP 4.58 / 4.61)."""

    def _valuation(self, cash_base: float, securities_base: float) -> PaperValuation:
        net_value = NetValue(
            as_of_date=_TRADE,
            currency=Currency.HKD,
            cash=cash_base,
            securities_value=securities_base,
            fees_paid=5.0,
        )
        return PaperValuation(
            as_of=_TRADE,
            base_currency=Currency.HKD,
            cash=(CashBalance(Currency.HKD, cash_base),),
            position_values=(),
            fees_paid=(CashBalance(Currency.HKD, 5.0),),
            cash_base=cash_base,
            securities_base=securities_base,
            fees_base=5.0,
            net_value=net_value,
            missing_prices=(),
        )

    def _mismatched_valuation(self) -> PaperValuation:
        # the components (185k) disagree with the net-value snapshot (190k)
        net_value = NetValue(
            as_of_date=_TRADE,
            currency=Currency.HKD,
            cash=90_000.0,
            securities_value=100_000.0,
            fees_paid=5.0,
        )
        return PaperValuation(
            as_of=_TRADE,
            base_currency=Currency.HKD,
            cash=(CashBalance(Currency.HKD, 85_000.0),),
            position_values=(),
            fees_paid=(CashBalance(Currency.HKD, 5.0),),
            cash_base=85_000.0,
            securities_base=100_000.0,
            fees_base=5.0,
            net_value=net_value,
            missing_prices=(),
        )

    def test_assets_close(self) -> None:
        valuation = self._valuation(cash_base=994_995.0, securities_base=5_500.0)
        result = reconcile_daily_assets(valuation=valuation)
        self.assertTrue(result.reconciled)
        self.assertTrue(valuation.reconciled())

    def test_mismatched_snapshot_detected(self) -> None:
        valuation = self._mismatched_valuation()
        self.assertFalse(valuation.reconciled())
        result = reconcile_daily_assets(valuation=valuation)
        self.assertFalse(result.reconciled)
        names = {difference.check_name for difference in result.differences}
        self.assertIn("assets_close", names)

    def test_difference_recorded_and_mapped(self) -> None:
        valuation = self._mismatched_valuation()
        result = reconcile_daily_assets(valuation=valuation)
        rows = reconciliation_rows(result, paper_run_id="paper-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["paper_run_id"], "paper-1")
        self.assertEqual(rows[0]["check_name"], "assets_close")
        self.assertEqual(rows[0]["as_of_date"], _TRADE)

    def test_difference_readable(self) -> None:
        difference = PaperReconciliationDifference(
            check_name="assets_close",
            expected=190_000.0,
            actual=200_000.0,
            detail="assets must equal cash + securities",
        )
        self.assertIn("assets_close", difference.readable())
