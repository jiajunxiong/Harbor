"""Paper execution, valuation and reconciliation suite (MVP 4 / SP 4.62-4.68).

Consolidated tests that bind the stage-4 modules together:

* 4.62 成交引擎 — fill price, slippage, volume constraint and partial fill
* 4.63 停牌/成交量约束 — suspension refusal, participation cap, unfilled defer
* 4.64 账本与估值 — multi-currency cash, fees, market value and net value
* 4.65 分红/企业行动 — dividends, splits, rights issues on the paper account
* 4.66 对账 — assets close, differences located and mapped to table rows
* 4.67 可重放 — identical inputs replay to identical orders/fills/valuations
* 4.68 多币种账本 — HKD/USD separate ledgers, explicit FX, missing FX refused
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_config import DividendConfig, FillRule, UnfilledPolicy
from harbor.core.backtest_domain import Currency, Market, NetValue, OrderSide
from harbor.core.backtest_interfaces import DailyQuote, Dividend
from harbor.core.corporate_actions import PositionAdjustment
from harbor.core.dividend_processing import CashDividend
from harbor.core.equity import ActionTerms, EntitlementEvent
from harbor.core.market_registry import CorporateActionType
from harbor.core.paper_account import (
    apply_paper_fill,
    convert_paper_cash,
    open_paper_account,
)
from harbor.core.paper_account_events import (
    apply_paper_corporate_action,
    apply_paper_dividend,
)
from harbor.core.paper_domain import (
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
)
from harbor.core.paper_execution_engine import execute_paper_order
from harbor.core.paper_reconciliation import (
    reconcile_daily_assets,
    reconcile_order_fill_account,
    reconciliation_rows,
)
from harbor.core.paper_replay import build_paper_execution_trace
from harbor.core.paper_valuation import PaperValuation, value_paper_account

_TRADE = date(2026, 1, 2)
_OPENED = date(2026, 1, 1)


def _utc_at(hour: int) -> datetime:
    return datetime(2026, 1, 2, hour, tzinfo=timezone.utc)


def _open() -> "object":
    return open_paper_account(
        account_id="acc-1",
        base_currency=Currency.HKD,
        currencies=(Currency.HKD, Currency.USD),
        initial_capital=1_000_000.0,
        opened_at=_OPENED,
    )


def _usd_open() -> "object":
    state = _open()
    return convert_paper_cash(
        state,
        from_currency=Currency.HKD,
        to_currency=Currency.USD,
        amount=200_000.0,
        rate=0.128,
    )


def _order(symbol: str = "0001.HK", market: Market = Market.HK, quantity: float = 100.0):
    return PaperOrder(
        order_id="order-1",
        paper_run_id="paper-1",
        market=market,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=quantity,
        currency=(Currency.HKD if market is Market.HK else Currency.USD),
        price_type=PaperPriceType.REFERENCE,
        status=PaperOrderStatus.CREATED,
        created_at=_utc_at(9),
        intention_id="sig-1",
    )


def _fill(symbol: str, market: Market, currency: Currency, quantity: float) -> PaperFill:
    return PaperFill(
        fill_id=f"fill-{symbol}",
        paper_order_id="order-1",
        paper_run_id="paper-1",
        market=market,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=quantity,
        price=50.0,
        currency=currency,
        trade_date=_TRADE,
        fee=5.0,
    )


def _quote(
    symbol: str = "0001.HK",
    market: Market = Market.HK,
    close: float = 50.0,
    volume: int = 1_000_000,
) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=_TRADE,
        open=close - 1.0,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _fx() -> "object":
    table = {(Currency.USD, Currency.HKD): 7.8}
    return lambda from_c, to_c, as_of: table.get((from_c, to_c))


class ExecutionEngineTests(unittest.TestCase):
    """成交引擎测试 (SP 4.62)."""

    def test_close_fill_price_with_slippage(self) -> None:
        result = execute_paper_order(
            order=_order(),
            day=_TRADE,
            quote=_quote(close=50.0),
            rule=FillRule.CLOSE,
            slippage_bps=20,
            fee=5.0,
        )
        self.assertTrue(result.executed)
        fill = result.fill
        assert fill is not None
        self.assertAlmostEqual(fill.price, 50.0 * (1 + 20 / 10_000))
        self.assertEqual(fill.quantity, 100.0)
        self.assertEqual(fill.fee, 5.0)

    def test_sell_slippage_moves_price_down(self) -> None:
        order = _order(quantity=10.0)
        order = PaperOrder(
            order_id=order.order_id,
            paper_run_id=order.paper_run_id,
            market=order.market,
            symbol=order.symbol,
            side=OrderSide.SELL,
            quantity=order.quantity,
            currency=order.currency,
            price_type=order.price_type,
            status=order.status,
            created_at=order.created_at,
            intention_id=order.intention_id,
        )
        result = execute_paper_order(
            order=order,
            day=_TRADE,
            quote=_quote(close=50.0),
            slippage_bps=20,
        )
        fill = result.fill
        assert fill is not None
        self.assertAlmostEqual(fill.price, 50.0 * (1 - 20 / 10_000))

    def test_volume_constraint_partial_fill(self) -> None:
        # 1% of 10_000 shares -> 100 shares, not the requested 500
        result = execute_paper_order(
            order=_order(quantity=500.0),
            day=_TRADE,
            quote=_quote(volume=10_000),
            participation_rate=0.01,
            volume=10_000,
            unfilled_policy=UnfilledPolicy.CANCEL,
        )
        fill = result.fill
        assert fill is not None
        self.assertEqual(fill.quantity, 100.0)
        self.assertIsNotNone(result.unfilled)

    def test_unfilled_policy_defer(self) -> None:
        result = execute_paper_order(
            order=_order(quantity=500.0),
            day=_TRADE,
            quote=_quote(volume=10_000),
            participation_rate=0.01,
            volume=10_000,
            unfilled_policy=UnfilledPolicy.DEFER,
        )
        self.assertIsNotNone(result.unfilled)
        self.assertEqual(result.unfilled.policy.value, "defer")


class SuspensionAndVolumeTests(unittest.TestCase):
    """停牌/成交量约束测试 (SP 4.63)."""

    def test_no_quote_refuses_execution(self) -> None:
        result = execute_paper_order(order=_order(), day=_TRADE, quote=None)
        self.assertFalse(result.executed)
        self.assertTrue(result.suspended)
        self.assertIsNone(result.fill)
        self.assertIn("suspended", result.refusal_reason or "")

    def test_zero_volume_refuses_execution(self) -> None:
        result = execute_paper_order(order=_order(), day=_TRADE, quote=_quote(volume=0))
        self.assertTrue(result.suspended)

    def test_participation_and_volume_required_together(self) -> None:
        from harbor.core.paper_execution_engine import PaperExecutionError

        with self.assertRaises(PaperExecutionError):
            execute_paper_order(
                order=_order(),
                day=_TRADE,
                quote=_quote(),
                participation_rate=0.1,
            )
        with self.assertRaises(PaperExecutionError):
            execute_paper_order(
                order=_order(),
                day=_TRADE,
                quote=_quote(),
                volume=100,
            )


class LedgerAndValuationTests(unittest.TestCase):
    """账本与估值测试 (SP 4.64)."""

    def test_multi_currency_cash_and_fees(self) -> None:
        state = _usd_open()
        state = apply_paper_fill(state, fill=_fill("AAPL", Market.US, Currency.USD, 100.0))
        # _usd_open converts 200k HKD at 0.128 USD/HKD -> 25,600 USD
        self.assertAlmostEqual(state.balance(Currency.USD), 25_600.0 - 100 * 50.0 - 5.0)
        self.assertEqual(state.fees(Currency.USD), 5.0)
        self.assertEqual(state.balance(Currency.HKD), 1_000_000.0 - 200_000.0)

    def test_valuation_net_value(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill("0001.HK", Market.HK, Currency.HKD, 100.0))
        quotes = {(Market.HK, "0001.HK"): _quote(close=55.0)}
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes=quotes,
            fx_rate=_fx(),
        )
        self.assertIsInstance(valuation, PaperValuation)
        self.assertAlmostEqual(valuation.cash_base, 1_000_000.0 - 5_000.0 - 5.0)
        self.assertAlmostEqual(valuation.securities_base, 100.0 * 55.0)
        self.assertAlmostEqual(
            valuation.total_base, valuation.cash_base + valuation.securities_base
        )
        self.assertTrue(valuation.reconciled())

    def test_valuation_missing_fx_refused(self) -> None:
        state = _usd_open()
        state = apply_paper_fill(state, fill=_fill("AAPL", Market.US, Currency.USD, 100.0))
        quotes = {(Market.US, "AAPL"): _quote("AAPL", Market.US, close=100.0)}
        with self.assertRaises(Exception):
            value_paper_account(account=state, as_of=_TRADE, quotes=quotes, fx_rate=lambda *_: None)


class CorporateEventTests(unittest.TestCase):
    """分红/企业行动模拟盘测试 (SP 4.65)."""

    def test_dividend_then_split_revalue(self) -> None:
        # split is a US action; the account is funded in USD via _usd_open
        state = _usd_open()
        state = apply_paper_fill(state, fill=_fill("AAPL", Market.US, Currency.USD, 100.0))
        dividend = Dividend(
            market=Market.US,
            symbol="AAPL",
            amount=0.5,
            currency=Currency.USD,
            ex_date=date(2026, 1, 10),
            record_date=date(2026, 1, 8),
            payment_date=date(2026, 1, 20),
        )
        state, payment = apply_paper_dividend(
            state,
            dividend=dividend,
            quantity=100.0,
            config=DividendConfig(include_special=True),
        )
        self.assertIsInstance(payment, CashDividend)
        self.assertAlmostEqual(state.balance(Currency.USD), 25_600.0 - 5_000.0 - 5.0 + 50.0)
        event = EntitlementEvent(
            action_id="ca-1",
            action_type=CorporateActionType.SPLIT,
            terms=ActionTerms(ratio=2.0),
            record_date=_TRADE,
        )
        state, adjustment = apply_paper_corporate_action(
            state,
            market=Market.US,
            symbol="AAPL",
            event=event,
            as_of_date=_TRADE,
        )
        self.assertIsInstance(adjustment, PositionAdjustment)
        self.assertEqual(state.position(Market.US, "AAPL").quantity, 200.0)

    def test_rights_issue_updates_quantity(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill("0001.HK", Market.HK, Currency.HKD, 100.0))
        event = EntitlementEvent(
            action_id="ca-2",
            action_type=CorporateActionType.RIGHTS_ISSUE,
            terms=ActionTerms(ratio=0.5),
            record_date=_TRADE,
        )
        state, _ = apply_paper_corporate_action(
            state,
            market=Market.HK,
            symbol="0001.HK",
            event=event,
            as_of_date=_TRADE,
        )
        self.assertEqual(state.position(Market.HK, "0001.HK").quantity, 50.0)


class ReconciliationTests(unittest.TestCase):
    """对账测试 (SP 4.66)."""

    def test_fill_consistent(self) -> None:
        state = _open()
        before = state
        after = apply_paper_fill(state, fill=_fill("0001.HK", Market.HK, Currency.HKD, 100.0))
        result = reconcile_order_fill_account(
            order=_order(),
            fill=_fill("0001.HK", Market.HK, Currency.HKD, 100.0),
            account_before=before,
            account_after=after,
        )
        self.assertTrue(result.reconciled)
        self.assertTrue(result.readable().startswith("reconciliation 2026-01-02"))

    def test_daily_assets_close(self) -> None:
        state = _open()
        state = apply_paper_fill(state, fill=_fill("0001.HK", Market.HK, Currency.HKD, 100.0))
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes={(Market.HK, "0001.HK"): _quote(close=50.0)},
            fx_rate=_fx(),
        )
        result = reconcile_daily_assets(valuation=valuation)
        self.assertTrue(result.reconciled)

    def test_difference_mapped_to_rows(self) -> None:
        state = _open()
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes={},
            fx_rate=_fx(),
        )
        # a deliberately broken snapshot diverges from the components
        broken = PaperValuation(
            as_of=valuation.as_of,
            base_currency=valuation.base_currency,
            cash=valuation.cash,
            position_values=valuation.position_values,
            fees_paid=valuation.fees_paid,
            cash_base=valuation.cash_base,
            securities_base=valuation.securities_base,
            fees_base=valuation.fees_base,
            net_value=NetValue(
                as_of_date=_TRADE,
                currency=Currency.HKD,
                cash=valuation.cash_base,
                securities_value=valuation.securities_base + 999.0,
                fees_paid=valuation.fees_base,
            ),
            missing_prices=valuation.missing_prices,
        )
        result = reconcile_daily_assets(valuation=broken)
        self.assertFalse(result.reconciled)
        rows = reconciliation_rows(result, paper_run_id="paper-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check_name"], "assets_close")
        self.assertEqual(rows[0]["paper_run_id"], "paper-1")


class ReplayTests(unittest.TestCase):
    """可重放测试 (SP 4.67)."""

    def _run(self) -> tuple[object, object]:
        orders = []
        fills = []
        state = _open()
        result = execute_paper_order(order=_order(), day=_TRADE, quote=_quote(close=50.0), fee=5.0)
        assert result.fill is not None
        orders.append(result.order_id)
        fills.append(result.fill)
        state = apply_paper_fill(state, fill=result.fill)
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes={(Market.HK, "0001.HK"): _quote(close=50.0)},
            fx_rate=_fx(),
        )
        return fills, valuation

    def test_replay_identical_fingerprints(self) -> None:
        fills_a, valuation_a = self._run()
        fills_b, valuation_b = self._run()
        trace_a = build_paper_execution_trace(
            run_id="paper-1",
            orders=(_order(),),
            fills=fills_a,
            valuations=(valuation_a,),
            audit_fingerprint="fp",
        )
        trace_b = build_paper_execution_trace(
            run_id="paper-2",
            orders=(_order(),),
            fills=fills_b,
            valuations=(valuation_b,),
            audit_fingerprint="fp",
        )
        self.assertEqual(trace_a.fingerprint(), trace_b.fingerprint())


class MultiCurrencyLedgerTests(unittest.TestCase):
    """多币种账本测试 (SP 4.68)."""

    def test_hkd_usd_separate_ledgers(self) -> None:
        state = _usd_open()
        self.assertAlmostEqual(state.balance(Currency.HKD), 800_000.0)
        self.assertAlmostEqual(state.balance(Currency.USD), 25_600.0)
        # USD cash is not 1:1 with HKD; assets need explicit FX
        self.assertNotAlmostEqual(state.balance(Currency.USD), 25_600.0 / 7.8)

    def test_explicit_fx_valuation(self) -> None:
        state = _usd_open()
        state = apply_paper_fill(state, fill=_fill("AAPL", Market.US, Currency.USD, 10.0))
        quotes = {(Market.US, "AAPL"): _quote("AAPL", Market.US, close=100.0)}
        valuation = value_paper_account(
            account=state,
            as_of=_TRADE,
            quotes=quotes,
            fx_rate=_fx(),
        )
        # USD 25_600 - 10*50 - 5 = 25_095 USD at 7.8 HKD/USD; market value 10*100
        expected_usd_cash = 25_600.0 - 10 * 50.0 - 5.0
        self.assertAlmostEqual(valuation.cash_base, 800_000.0 + expected_usd_cash * 7.8)
        self.assertAlmostEqual(valuation.securities_base, 10 * 100.0 * 7.8)

    def test_missing_fx_refused_not_assumed(self) -> None:
        state = _usd_open()
        with self.assertRaises(Exception):
            value_paper_account(
                account=state,
                as_of=_TRADE,
                quotes={},
                fx_rate=lambda *_: None,
            )

    def test_convert_cash_explicit(self) -> None:
        state = _usd_open()
        state = convert_paper_cash(
            state,
            from_currency=Currency.USD,
            to_currency=Currency.HKD,
            amount=10_000.0,
            rate=7.8,
        )
        self.assertAlmostEqual(state.balance(Currency.HKD), 800_000.0 + 10_000.0 * 7.8)
        self.assertAlmostEqual(state.balance(Currency.USD), 25_600.0 - 10_000.0)

    def test_overdraw_refused(self) -> None:
        state = _usd_open()
        with self.assertRaises(Exception):
            apply_paper_fill(
                state,
                fill=_fill("AAPL", Market.US, Currency.USD, 10_000.0),
            )
