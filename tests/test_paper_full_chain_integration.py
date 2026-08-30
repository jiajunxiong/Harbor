"""Paper full-chain integration test (MVP 4 / SP 4.90).

Runs the complete paper loop over fixed Mock data for HK, US and a
cross-market run — strategy signal -> order drafts -> order rules -> execution
-> account ledger -> daily valuation -> reconciliation — and asserts the
results are day-by-day reconcilable and replayable (SP 4.59 / 4.81). The
account always closes: cash + position market value == total, so every day's
equity path can be audited.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_config import FillRule
from harbor.core.backtest_domain import Currency, Market, NetValue
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.paper_account import (
    AccountAssets,
    account_assets_base,
    apply_paper_fill,
    open_paper_account,
)
from harbor.core.paper_domain import PaperOrder, PaperOrderStatus, PaperPriceType
from harbor.core.paper_execution_engine import execute_paper_order
from harbor.core.paper_order_drafts import derive_order_drafts
from harbor.core.paper_order_rules import apply_paper_order_rule
from harbor.core.paper_reconciliation import reconcile_daily_assets
from harbor.core.paper_replay import (
    PaperExecutionTrace,
    build_paper_execution_trace,
)
from harbor.core.paper_signal import build_signal_intention
from harbor.core.paper_target_portfolio import derive_target_portfolio
from harbor.core.paper_valuation import PaperValuation, value_paper_account

_DAY1 = date(2026, 1, 2)
_DAY2 = date(2026, 1, 5)


def _utc_at(hour: int) -> datetime:
    return datetime(2026, 1, 2, hour, tzinfo=timezone.utc)


def _quote(market: Market, symbol: str, close: float, volume: int = 1_000_000) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=_DAY1,
        open=close - 1.0,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _fx_hkd() -> dict[tuple[Currency, Currency], float]:
    return {(Currency.USD, Currency.HKD): 7.8}


def _build_orders(
    *,
    run_id: str,
    market: Market,
    base_currency: Currency,
    initial_capital: float,
    targets: tuple[tuple[str, float], ...],
    prices: dict[str, float],
    fx_rate: object | None = None,
) -> tuple[tuple[PaperOrder, ...], str]:
    """Derive and align paper orders from a signal (SP 4.14-4.18)."""
    symbol, weight = targets[0]
    intention = build_signal_intention(
        intention_id="sig-1",
        paper_run_id=run_id,
        strategy="paper-demo",
        strategy_version="1.0.0",
        market=market,
        rebalance_date=_DAY1,
        target_weights=targets,
        source_run_id="oos-1",
        created_at=_utc_at(9),
    )
    if fx_rate is None:

        def _identity_fx(_f: Currency, _t: Currency, _d: date) -> float:
            return 1.0

        fx_rate = _identity_fx
    target = derive_target_portfolio(
        signal=intention,
        positions={},
        prices={(market, sym): prices[sym] for sym, _w in targets},
        portfolio_value=initial_capital,
        available_cash=initial_capital,
        base_currency=base_currency,
        fx_rate=fx_rate,  # type: ignore[arg-type]
    )
    drafts = derive_order_drafts(
        target=target,
        positions={},
        available_cash=initial_capital,
        price_type=PaperPriceType.REFERENCE,
    )
    orders: list[PaperOrder] = []
    for draft in drafts.drafts:
        outcome = apply_paper_order_rule(draft.market, draft.quantity)
        if not outcome.ok:
            continue
        orders.append(
            PaperOrder(
                order_id=f"order-{draft.symbol}",
                paper_run_id=run_id,
                market=draft.market,
                symbol=draft.symbol,
                side=draft.side,
                quantity=outcome.quantity,
                currency=draft.currency,
                price_type=draft.price_type,
                status=PaperOrderStatus.CREATED,
                created_at=_utc_at(9),
                intention_id="sig-1",
            )
        )
    return tuple(orders), symbol


def _run_market(
    market: Market,
    base: Currency,
    currencies: tuple[Currency, ...],
    capital: float,
    symbol: str,
    price: float,
) -> tuple[AccountAssets, PaperValuation, PaperExecutionTrace | None]:
    """Run one market's full paper loop over day 1 (SP 4.90)."""
    run_id = f"run-{market.value}"
    account = open_paper_account(
        account_id="acc-1",
        base_currency=base,
        currencies=currencies,
        initial_capital=capital,
        opened_at=_DAY1,
    )
    orders, _symbol = _build_orders(
        run_id=run_id,
        market=market,
        base_currency=base,
        initial_capital=capital,
        targets=((symbol, 0.5),),
        prices={symbol: price},
    )
    fills = []
    for order in orders:
        result = execute_paper_order(
            order=order,
            day=_DAY1,
            quote=_quote(market, order.symbol, price),
            rule=FillRule.CLOSE,
            fee=5.0,
        )
        assert result.fill is not None
        fills.append(result.fill)
        account = apply_paper_fill(account, fill=result.fill)
    quotes = {(market, symbol): _quote(market, symbol, price)}
    valuation = value_paper_account(
        account=account,
        as_of=_DAY1,
        quotes=quotes,
        fx_rate=lambda _f, _t, _d: 1.0,
    )
    assets = account_assets_base(account, fx_rate=lambda _f, _t: 1.0 if _t is base else None)
    trace = build_paper_execution_trace(
        run_id=run_id,
        orders=orders,
        fills=tuple(fills),
        valuations=(valuation,),
        audit_fingerprint="fp",
    )
    return assets, valuation, trace


class HkFullChainTests(unittest.TestCase):
    """The HK full paper loop (SP 4.90)."""

    def test_hk_day_reconciles(self) -> None:
        assets, valuation, _trace = _run_market(
            Market.HK,
            Currency.HKD,
            (Currency.HKD,),
            1_000_000.0,
            "0001.HK",
            50.0,
        )
        self.assertTrue(valuation.reconciled())
        self.assertAlmostEqual(
            valuation.total_base, valuation.cash_base + valuation.securities_base
        )
        self.assertTrue(assets.reconciled())
        result = reconcile_daily_assets(valuation=valuation)
        self.assertTrue(result.reconciled)
        # 0.5 weight * 1M / 50 = 10,000 shares @ 50 = 500,000
        self.assertAlmostEqual(valuation.securities_base, 500_000.0)

    def test_hk_replayable(self) -> None:
        _a1, _v1, trace1 = _run_market(
            Market.HK,
            Currency.HKD,
            (Currency.HKD,),
            1_000_000.0,
            "0001.HK",
            50.0,
        )
        _a2, _v2, trace2 = _run_market(
            Market.HK,
            Currency.HKD,
            (Currency.HKD,),
            1_000_000.0,
            "0001.HK",
            50.0,
        )
        assert trace1 is not None and trace2 is not None
        self.assertEqual(trace1.fingerprint(), trace2.fingerprint())


class UsFullChainTests(unittest.TestCase):
    """The US full paper loop (SP 4.90)."""

    def test_us_day_reconciles(self) -> None:
        assets, valuation, _trace = _run_market(
            Market.US,
            Currency.USD,
            (Currency.USD,),
            130_000.0,
            "AAPL.US",
            100.0,
        )
        self.assertTrue(valuation.reconciled())
        self.assertTrue(assets.reconciled())
        result = reconcile_daily_assets(valuation=valuation)
        self.assertTrue(result.reconciled)
        # 0.5 weight * 130,000 / 100 = 650 shares @ 100 = 65,000
        self.assertAlmostEqual(valuation.securities_base, 65_000.0)

    def test_us_order_rule_keeps_fractional(self) -> None:
        orders, _symbol = _build_orders(
            run_id="run-US",
            market=Market.US,
            base_currency=Currency.USD,
            initial_capital=130_000.0,
            targets=(("AAPL.US", 0.5),),
            prices={"AAPL.US": 99.9},
        )
        self.assertEqual(len(orders), 1)
        # 0.5 * 130,000 / 99.9 = 650.65 shares (US fractional allowed)
        self.assertAlmostEqual(orders[0].quantity, 650.6506506506507)


class CrossMarketFullChainTests(unittest.TestCase):
    """The cross-market (HK + US) full paper loop (SP 4.90)."""

    def test_cross_market_assets_reconcile_with_explicit_fx(self) -> None:
        base = Currency.HKD
        account = open_paper_account(
            account_id="acc-1",
            base_currency=base,
            currencies=(Currency.HKD, Currency.USD),
            initial_capital=1_000_000.0,
            opened_at=_DAY1,
        )
        # fund USD by an explicit conversion (SP 4.4, no implicit 1:1)
        from harbor.core.paper_account import convert_paper_cash

        account = convert_paper_cash(
            account,
            from_currency=Currency.HKD,
            to_currency=Currency.USD,
            amount=200_000.0,
            rate=0.128,
        )
        hk_orders, _ = _build_orders(
            run_id="run-cross",
            market=Market.HK,
            base_currency=base,
            initial_capital=1_000_000.0,
            targets=(("0001.HK", 0.2),),
            prices={"0001.HK": 50.0},
        )
        us_orders, _ = _build_orders(
            run_id="run-cross",
            market=Market.US,
            base_currency=base,
            initial_capital=1_000_000.0,
            targets=(("AAPL.US", 0.19),),
            prices={"AAPL.US": 100.0},
            fx_rate=lambda _f, _t, _d: _fx_hkd().get((_f, _t)),
        )
        # HK buys in HKD (funded by the remaining HKD cash)
        for order in hk_orders:
            result = execute_paper_order(
                order=order,
                day=_DAY1,
                quote=_quote(Market.HK, "0001.HK", 50.0),
                rule=FillRule.CLOSE,
                fee=5.0,
            )
            assert result.fill is not None
            account = apply_paper_fill(account, fill=result.fill)
        # US buys need USD cash: fund a USD position from the explicit USD cash
        us_fill = execute_paper_order(
            order=us_orders[0],
            day=_DAY1,
            quote=_quote(Market.US, "AAPL.US", 100.0),
            rule=FillRule.CLOSE,
            fee=5.0,
        )
        assert us_fill.fill is not None
        account = apply_paper_fill(account, fill=us_fill.fill)

        valuation = value_paper_account(
            account=account,
            as_of=_DAY1,
            quotes={
                (Market.HK, "0001.HK"): _quote(Market.HK, "0001.HK", 50.0),
                (Market.US, "AAPL.US"): _quote(Market.US, "AAPL.US", 100.0),
            },
            fx_rate=lambda _f, _t, _d: _fx_hkd().get((_f, _t)),
        )
        self.assertTrue(valuation.reconciled())
        result = reconcile_daily_assets(valuation=valuation)
        self.assertTrue(result.reconciled)

    def test_cross_market_missing_fx_refuses(self) -> None:
        account = open_paper_account(
            account_id="acc-1",
            base_currency=Currency.HKD,
            currencies=(Currency.HKD, Currency.USD),
            initial_capital=1_000_000.0,
            opened_at=_DAY1,
        )
        from harbor.core.paper_account import convert_paper_cash

        account = convert_paper_cash(
            account,
            from_currency=Currency.HKD,
            to_currency=Currency.USD,
            amount=200_000.0,
            rate=0.128,
        )
        with self.assertRaises(Exception):
            value_paper_account(
                account=account,
                as_of=_DAY1,
                quotes={},
                fx_rate=lambda *_: None,
            )

    def test_net_value_reconstructed(self) -> None:
        assets, valuation, _trace = _run_market(
            Market.HK,
            Currency.HKD,
            (Currency.HKD,),
            1_000_000.0,
            "0001.HK",
            50.0,
        )
        net: NetValue = valuation.net_value
        self.assertAlmostEqual(net.total_value, valuation.cash_base + valuation.securities_base)
