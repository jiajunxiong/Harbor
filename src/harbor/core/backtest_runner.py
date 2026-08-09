"""Small end-to-end backtest runner (MVP 2 / SP 2.51).

Wires the SP 2.47 pipeline (预检 → 信号 → 调仓 → 成交 → 企业行动 → 估值 → 持久化)
with the Phase-3 domain primitives into a concrete, replayable end-to-end
backtest driven by a fixed, deterministic Mock universe (固定 Mock 数据). It
runs the three required combinations — HK-only, US-only and cross-market
(HK+US with an explicit FX rate) — and records a day-by-day trace whose net
value, cash and positions reconcile (结果可逐日核算).

Each trading day runs the canonical pipeline through :func:`run_backtest`
(SP 2.47) and the SP 2.46 state machine, so a stage that raises (e.g. missing
FX refuses a cross-market valuation) fails that day's run while retaining the
diagnostics generated so far. The rebalance stage consumes a fixed selection
snapshot (the factor pipeline is out of scope here and tested separately in
Phase 2); target weights, concentration, order drafts, fills, costs, volume
limits, suspensions, dividends, corporate actions and daily valuation all use
the SP 2.33–2.45 modules, so the end-to-end path exercises every execution
rule with deterministic, replayable results.

Pure core logic: depends only on the domain, config, engine, identity and the
Phase-3 modules; never touches storage or CLI code.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date

from harbor.core.backtest_config import BacktestConfig, MarketQuota
from harbor.core.backtest_domain import (
    Currency,
    Fill,
    Market,
    Order,
    OrderSide,
    Position,
)
from harbor.core.backtest_engine import (
    BacktestStage,
    BacktestStep,
    run_backtest,
)
from harbor.core.backtest_interfaces import DailyQuote, Dividend, TradingCalendar
from harbor.core.backtest_state_machine import RunState
from harbor.core.concentration import apply_concentration_constraints
from harbor.core.corporate_actions import PositionAdjustment, apply_corporate_action
from harbor.core.cross_market_merge import MergedSelection, merge_selections
from harbor.core.dividend_processing import CashDividend, pay_dividend
from harbor.core.equity import EntitlementEvent
from harbor.core.fill_price import resolve_fill_price, simulate_fill
from harbor.core.ledger import Ledger, apply_fill, convert, credit, deposit, empty_ledger
from harbor.core.market_selector import SelectionRank, SelectionResult
from harbor.core.order_drafts import generate_order_drafts
from harbor.core.run_identity import RunIdentity, identity_from_config
from harbor.core.run_logging import RunLogContext, log_run_event
from harbor.core.suspension import (
    PositionValuation,
    RefusedOrder,
    position_valuation_price,
    refuse_order,
)
from harbor.core.target_weight import TargetWeightConfig, compute_target_weights
from harbor.core.valuation import DailyValuation, value_portfolio
from harbor.core.volume_limit import apply_volume_limit


@dataclass(frozen=True)
class MockUniverse:
    """Fixed, deterministic market data for the small end-to-end backtest.

    Quotes are keyed by ``(market, symbol)`` and map each trading day to a
    :class:`DailyQuote`; dividends and corporate actions are per
    ``(market, symbol)``; FX rates are keyed by ``(from_currency, to_currency)``
    and map each date to the latest known rate; ``selections`` provides the
    fixed per-market selection snapshot for each rebalance date. All data is
    immutable and replayed identically on every run (SP 2.48/2.62).
    """

    calendar: TradingCalendar
    quotes: Mapping[tuple[Market, str], Mapping[date, DailyQuote]]
    dividends: Mapping[tuple[Market, str], tuple[Dividend, ...]] = field(default_factory=dict)
    corporate_actions: Mapping[tuple[Market, str], tuple[EntitlementEvent, ...]] = field(
        default_factory=dict
    )
    fx_rates: Mapping[tuple[Currency, Currency], Mapping[date, float]] = field(default_factory=dict)
    selections: Mapping[tuple[Market, date], tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class DailyResult:
    """The day-by-day outcome of one trading day (SP 2.51).

    ``valuation`` carries the SP 2.45 net value; ``fills``, ``dividends`` and
    ``adjustments`` record what happened on the day; ``refused`` carries the
    SP 2.41 rejected-trade trail; ``warnings`` accumulate across stages.
    """

    as_of: date
    valuation: DailyValuation
    fills: tuple[Fill, ...]
    dividends: tuple[CashDividend, ...]
    adjustments: tuple[PositionAdjustment, ...]
    refused: tuple[RefusedOrder, ...]
    warnings: tuple[str, ...]

    def reconcile(self) -> tuple[str, ...]:
        """Return reconciliation failures, empty when balanced (SP 2.51)."""
        failures: list[str] = []
        net = self.valuation.net_value
        expected = net.cash + net.securities_value
        if abs(net.total_value - expected) > 1e-6:
            failures.append(
                f"{self.as_of.isoformat()}: net value {net.total_value:.2f} != "
                f"cash {net.cash:.2f} + securities {net.securities_value:.2f}"
            )
        for position in self.valuation.position_values:
            expected = position.quantity * position.price * position.fx_rate
            if abs(position.market_value_base - expected) > 1e-6:
                failures.append(
                    f"{self.as_of.isoformat()}: {position.symbol} market value "
                    f"{position.market_value_base:.2f} != qty {position.quantity:.4f} "
                    f"x price {position.price:.4f} x fx {position.fx_rate:.4f}"
                )
        return tuple(failures)


@dataclass(frozen=True)
class BacktestTrace:
    """The full end-to-end run outcome with its day-by-day results (SP 2.51)."""

    run_id: str
    config: BacktestConfig
    identity: RunIdentity
    state: RunState
    results: tuple[DailyResult, ...]

    @property
    def succeeded(self) -> bool:
        """Whether the run reached COMPLETED."""
        return self.state.status.value == "COMPLETED"

    def reconcile_all(self) -> tuple[str, ...]:
        """Return reconciliation failures across every day (SP 2.51)."""
        failures: list[str] = []
        for result in self.results:
            failures.extend(result.reconcile())
        return tuple(failures)

    def net_values(self) -> tuple[tuple[date, float], ...]:
        """Return the ``(day, total net value)`` series, in date order."""
        return tuple(
            (result.as_of, result.valuation.net_value.total_value) for result in self.results
        )

    def readable(self) -> str:
        """Render the run outcome as a human-readable summary."""
        lines = [
            f"end-to-end backtest {self.run_id}: {self.state.status.value}",
            f"  identity: {self.identity.fingerprint()}",
            f"  days: {len(self.results)}",
        ]
        for result in self.results:
            lines.append(
                f"  {result.as_of.isoformat()}: "
                f"net {result.valuation.net_value.total_value:.2f} "
                f"(cash {result.valuation.net_value.cash:.2f}, "
                f"securities {result.valuation.net_value.securities_value:.2f})"
            )
        return "\n".join(lines)


class _RunContext:
    """Mutable execution state threaded through the day loop (internal)."""

    def __init__(
        self,
        *,
        config: BacktestConfig,
        universe: MockUniverse,
        weighting: TargetWeightConfig = TargetWeightConfig(),
    ) -> None:
        self.config = config
        self.universe = universe
        self.weighting = weighting
        self.ledger: Ledger = empty_ledger(
            as_of=config.start_date, base_currency=config.base_currency
        )
        self.ledger = deposit(
            self.ledger,
            currency=config.base_currency,
            amount=config.initial_capital,
            base_rate=1.0,
        )
        self.positions: dict[tuple[Market, str], Position] = {}
        self.results: list[DailyResult] = []
        self.warnings: list[str] = []
        self._fills: list[Fill] = []
        self._dividends: list[CashDividend] = []
        self._adjustments: list[PositionAdjustment] = []
        self._refused: list[RefusedOrder] = []
        self._pending: list[tuple[Order, date]] = []
        self._valuation: DailyValuation | None = None

    # -- helpers -----------------------------------------------------------

    def _quote(self, market: Market, symbol: str, day: date) -> DailyQuote | None:
        return self.universe.quotes.get((market, symbol), {}).get(day)

    def _last_quote(self, market: Market, symbol: str, day: date) -> DailyQuote | None:
        quotes = self.universe.quotes.get((market, symbol), {})
        candidates = [d for d in quotes if d < day]
        if not candidates:
            return None
        return quotes[max(candidates)]

    def _fx_rate(self, from_currency: Currency, to_currency: Currency, as_of: date) -> float | None:
        if from_currency is to_currency:
            return 1.0
        rates = self.universe.fx_rates.get((from_currency, to_currency), {})
        candidates = [d for d in rates if d <= as_of]
        if not candidates:
            return None
        return rates[max(candidates)]

    def _rebalance_days(self, market: Market) -> set[date]:
        return {day for (m, day) in self.universe.selections if m is market}

    # -- pipeline stages ---------------------------------------------------

    def signal(self, day: date) -> Sequence[str]:
        """Signal stage: nothing to compute, the selection is fixed (SP 2.51)."""
        return ()

    def rebalance(self, day: date) -> Sequence[str]:
        """Rebalance stage: draft orders for every market rebalancing today.

        All markets with a fixed selection for ``day`` are merged into a single
        cross-market selection (SP 2.27) so the configured market quotas and FX
        apply at the portfolio level; the merged weights then flow through
        concentration (SP 2.35) and order drafts (SP 2.36).
        """
        warnings: list[str] = []
        rebalancing = [
            quota
            for quota in self.config.market_quotas
            if day in self._rebalance_days(quota.market)
        ]
        if not rebalancing:
            return ()
        merged = self._merge_all(day, rebalancing)
        target = compute_target_weights(merged, self.weighting)
        constrained = apply_concentration_constraints(target, self.config.risk)
        portfolio_value = self._portfolio_value()
        available_cash = self.ledger.balance(self.config.base_currency)
        drafts = generate_order_drafts(
            constrained,
            self._position_quantities(),
            self._prices(day),
            portfolio_value=portfolio_value,
            available_cash=available_cash,
            fx_rate=self._fx_rate,
        )
        for draft in drafts.drafts:
            order = Order(
                symbol=draft.symbol,
                market=draft.market,
                side=draft.side,
                quantity=draft.quantity,
                currency=draft.currency,
                trade_date=day,
                ref=f"rebalance:{day.isoformat()}",
            )
            self._pending.append((order, day))
        if drafts.cash_shortfall > 0:
            warnings.append(f"{day.isoformat()}: cash shortfall {drafts.cash_shortfall:.2f}.")
        return tuple(warnings)

    def fill(self, day: date) -> Sequence[str]:
        """Fill stage: execute the orders scheduled for today (SP 2.39–2.41).

        Sell orders are realized before buy orders so that a day which is
        affordable in aggregate (buys <= cash + sell proceeds, SP 2.36) never
        overdraws cash mid-fill: the cash from sells funds the day's buys
        instead of a symbol-ordered buy hitting a temporary negative balance
        (SP 2.42). The order is stable within each side, so the run stays
        deterministic and replayable.
        """
        warnings: list[str] = []
        due = [entry for entry in self._pending if entry[1] <= day]
        self._pending = [entry for entry in self._pending if entry[1] > day]
        due.sort(key=lambda entry: 0 if entry[0].side is OrderSide.SELL else 1)
        for order, _fill_day in due:
            quote = self._quote(order.market, order.symbol, day)
            refused = refuse_order(order=order, day=day, quote=quote)
            if refused is not None:
                self._refused.append(refused)
                warnings.append(refused.reason)
                continue
            assert quote is not None
            reference = resolve_fill_price(rule=self.config.fill.fill_rule, quote=quote)
            volume = apply_volume_limit(
                order=order,
                reference_price=reference,
                volume=quote.volume,
                participation_rate=self.config.volume.participation_rate,
                policy=self.config.volume.on_unfilled,
            )
            if volume.filled_quantity <= 0:
                warnings.append(volume.reason)
                continue
            reduced = replace(order, quantity=volume.filled_quantity)
            fill = simulate_fill(
                order=reduced,
                rule=self.config.fill.fill_rule,
                quote=quote,
                config=self.config.cost,
            )
            if not self._fund_fill(fill):
                warnings.append(f"{day.isoformat()}: no base cash to fund {fill.symbol}.")
                continue
            self.ledger = apply_fill(self.ledger, fill=fill)
            self._apply_fill_position(fill)
            self._fills.append(fill)
            if not volume.is_full:
                warnings.append(volume.reason)
        return tuple(warnings)

    def _fund_fill(self, fill: Fill) -> bool:
        """Ensure the fill's own currency cash exists; refuse when unfundable.

        When the fill currency differs from the base currency, the base cash is
        converted into the fill currency at the day's FX rate so the buy can
        pay in its own quote currency (SP 2.42). No implicit FX: a missing or
        non-positive rate refuses the fill rather than assuming 1:1.

        A base-currency buy must be covered by the current base cash. Sells
        are realized before buys in the fill stage, so a base-currency buy that
        is still unfundable reflects a genuine rebalance cash shortfall
        (SP 2.36); it is refused with a warning rather than raising
        :class:`~harbor.core.ledger.InsufficientCashError` and failing the run.
        """
        base = self.config.base_currency
        cost = fill.quantity * fill.price + fill.fee
        if fill.currency is base:
            if fill.side is OrderSide.SELL:
                return True
            # A tiny buffer avoids float-rounding leaving the balance slightly
            # short of the fill's notional + fee (SP 2.42).
            return self.ledger.balance(base) >= cost * (1.0 + 1e-9)
        rate = self._fx_rate(fill.currency, base, fill.trade_date)
        if rate is None or rate <= 0:
            return False
        # A tiny buffer avoids float-rounding leaving the converted balance
        # slightly short of the fill's notional + fee (SP 2.42).
        base_needed = cost * rate * (1.0 + 1e-9)
        if self.ledger.balance(base) < base_needed:
            return False
        self.ledger = convert(
            self.ledger,
            from_currency=base,
            to_currency=fill.currency,
            amount=base_needed,
            rate=1.0 / rate,
        )
        return True

    def corporate_action(self, day: date) -> Sequence[str]:
        """Corporate action stage: pay dividends and apply actions (SP 2.43/2.44)."""
        warnings: list[str] = []
        for (market, symbol), position in list(self.positions.items()):
            for dividend in self.universe.dividends.get((market, symbol), ()):
                payment_date = dividend.payment_date or dividend.ex_date
                if payment_date != day:
                    continue
                self.ledger, payment = pay_dividend(
                    self.ledger,
                    dividend=dividend,
                    quantity=position.quantity,
                    config=self.config.dividend,
                )
                if payment is not None:
                    self._dividends.append(payment)
            for event in self.universe.corporate_actions.get((market, symbol), ()):
                effective = event.ex_date or event.record_date
                if effective != day:
                    continue
                adjustment = apply_corporate_action(position, event)
                self._adjustments.append(adjustment)
                updated = replace(position, quantity=adjustment.new_quantity)
                if adjustment.new_quantity <= 0:
                    del self.positions[(market, symbol)]
                else:
                    self.positions[(market, symbol)] = updated
                if adjustment.cash_amount != 0.0:
                    self.ledger = credit(
                        self.ledger,
                        currency=position.currency,
                        amount=adjustment.cash_amount,
                    )
        return tuple(warnings)

    def valuation(self, day: date) -> Sequence[str]:
        """Valuation stage: compute the daily net value (SP 2.45).

        Stores the valuation for the persist stage and returns suspension
        carry-forward warnings (SP 2.41) as the stage's warnings.
        """
        valuations: dict[tuple[Market, str], PositionValuation] = {}
        warnings: list[str] = []
        for (market, symbol), position in self.positions.items():
            quote = self._quote(market, symbol, day)
            valuation = position_valuation_price(
                market=market,
                symbol=symbol,
                day=day,
                quote=quote,
                last_quote=self._last_quote(market, symbol, day),
                config=self.config.suspension,
            )
            valuations[(market, symbol)] = valuation
            if valuation.warning is not None:
                warnings.append(valuation.warning.message)
        self._valuation = value_portfolio(
            as_of=day,
            base_currency=self.config.base_currency,
            ledger=self.ledger,
            positions=tuple(self.positions.values()),
            valuations=valuations,
            fx_rate=self._fx_rate,
        )
        return tuple(warnings)

    def persist(self, day: date) -> Sequence[str]:
        """Persist stage: record the day's result (SP 2.51)."""
        valuation = self._valuation
        assert valuation is not None
        self.results.append(
            DailyResult(
                as_of=day,
                valuation=valuation,
                fills=tuple(self._fills),
                dividends=tuple(self._dividends),
                adjustments=tuple(self._adjustments),
                refused=tuple(self._refused),
                warnings=tuple(self.warnings),
            )
        )
        self._fills = []
        self._dividends = []
        self._adjustments = []
        self._refused = []
        self._valuation = None
        self.warnings = []
        return ()

    # -- internal helpers ---------------------------------------------------

    def _merge_all(
        self,
        day: date,
        rebalancing: Sequence[MarketQuota],
    ) -> MergedSelection:
        """Merge the fixed selections of all rebalancing markets (SP 2.27)."""
        selections: dict[Market, SelectionResult] = {}
        for quota in rebalancing:
            selected = self.universe.selections.get((quota.market, day), ())
            selections[quota.market] = SelectionResult(
                market=quota.market,
                as_of=day,
                target_count=quota.target_count,
                candidates=tuple(selected),
                selected=tuple(selected),
                rankings=tuple(
                    SelectionRank(
                        symbol=symbol,
                        score=float(len(selected) - index),
                        rank=index + 1,
                        selected=True,
                    )
                    for index, symbol in enumerate(selected)
                ),
            )
        return merge_selections(
            as_of=day,
            base_currency=self.config.base_currency,
            quotas=tuple(rebalancing),
            selections=selections,
            fx_rate=self._fx_rate,
        )

    def _position_quantities(self) -> dict[tuple[Market, str], float]:
        return {
            (market, symbol): position.quantity
            for (market, symbol), position in self.positions.items()
        }

    def _prices(self, day: date) -> dict[tuple[Market, str], float]:
        prices: dict[tuple[Market, str], float] = {}
        for (market, symbol), position in self.positions.items():
            quote = self._quote(market, symbol, day)
            if quote is None:
                quote = self._last_quote(market, symbol, day)
            if quote is not None:
                prices[(market, symbol)] = quote.close
        for (market, day_key), symbols in self.universe.selections.items():
            if day_key != day:
                continue
            for symbol in symbols:
                if (market, symbol) not in prices:
                    quote = self._quote(market, symbol, day)
                    if quote is not None:
                        prices[(market, symbol)] = quote.close
        return prices

    def _portfolio_value(self) -> float:
        if self.results:
            return self.results[-1].valuation.net_value.total_value
        return self.config.initial_capital

    def _apply_fill_position(self, fill: Fill) -> None:
        key = (fill.market, fill.symbol)
        current = self.positions.get(key)
        if fill.side is OrderSide.BUY:
            if current is None:
                self.positions[key] = Position(
                    symbol=fill.symbol,
                    market=fill.market,
                    quantity=fill.quantity,
                    average_cost=fill.price,
                    currency=fill.currency,
                    as_of_date=fill.trade_date,
                )
            else:
                new_quantity = current.quantity + fill.quantity
                new_cost = (
                    current.quantity * current.average_cost + fill.quantity * fill.price
                ) / new_quantity
                self.positions[key] = replace(
                    current,
                    quantity=new_quantity,
                    average_cost=new_cost,
                    as_of_date=fill.trade_date,
                )
        else:
            if current is None:
                return
            new_quantity = current.quantity - fill.quantity
            if new_quantity <= 1e-9:
                del self.positions[key]
            else:
                self.positions[key] = replace(
                    current, quantity=new_quantity, as_of_date=fill.trade_date
                )


def _trading_days(config: BacktestConfig, universe: MockUniverse) -> tuple[date, ...]:
    """Return the sorted union of trading days across the configured markets."""
    days: set[date] = set()
    for quota in config.market_quotas:
        days.update(
            universe.calendar.trading_days(quota.market, config.start_date, config.end_date)
        )
    return tuple(sorted(days))


def _bind(
    handler: Callable[[date], Sequence[str]],
    day: date,
) -> Callable[[], Sequence[str]]:
    """Bind a day-scoped stage handler to a no-argument step callable."""
    return lambda: handler(day)


def run_end_to_end_backtest(
    *,
    run_id: str,
    config: BacktestConfig,
    universe: MockUniverse,
    data_cutoff: date | None = None,
    code_version: str = "1.0.0",
    weighting: TargetWeightConfig = TargetWeightConfig(),
    log_context: RunLogContext | None = None,
    logger: logging.Logger | None = None,
) -> BacktestTrace:
    """Run the small end-to-end backtest day by day (SP 2.51).

    Each trading day runs the canonical SP 2.47 pipeline (signal → rebalance →
    fill → corporate action → valuation → persist) through :func:`run_backtest`;
    a stage that raises fails that day at its stage, retaining the diagnostics
    generated so far (SP 2.46). The identity (SP 2.48) is computed from the
    configuration, the data cutoff (defaults to the config end date) and the
    code version.

    When ``log_context`` and ``logger`` are both supplied, each stage emits
    structured ``backtest_stage_*`` events carrying the run correlation fields
    (SP 2.71); otherwise no logging is performed.

    Args:
        run_id: The run identifier.
        config: The validated backtest configuration (SP 2.4).
        universe: The fixed Mock data (SP 2.51).
        data_cutoff: The data cutoff date (defaults to ``config.end_date``).
        code_version: The code version.
        weighting: The target-weight rule (default equal weight, no cash).
        log_context: Optional run-log correlation context (SP 2.71).
        logger: Optional structured logger for stage events (SP 2.71).

    Returns:
        A :class:`BacktestTrace` with the day-by-day results.
    """
    context = _RunContext(config=config, universe=universe, weighting=weighting)
    cutoff = data_cutoff if data_cutoff is not None else config.end_date
    identity = identity_from_config(config=config, data_cutoff=cutoff, code_version=code_version)
    state: RunState | None = None
    for day in _trading_days(config, universe):
        steps = [
            BacktestStep(BacktestStage.SIGNAL, _bind(context.signal, day)),
            BacktestStep(BacktestStage.REBALANCE, _bind(context.rebalance, day)),
            BacktestStep(BacktestStage.FILL, _bind(context.fill, day)),
            BacktestStep(
                BacktestStage.CORPORATE_ACTION,
                _bind(context.corporate_action, day),
            ),
            BacktestStep(BacktestStage.VALUATION, _bind(context.valuation, day)),
            BacktestStep(BacktestStage.PERSIST, _bind(context.persist, day)),
        ]
        if log_context is not None and logger is not None:
            steps = _logged_steps(steps, logger=logger, log_context=log_context)
        run = run_backtest(run_id=run_id, steps=steps)
        state = run.state
        if not run.succeeded:
            break
    assert state is not None
    return BacktestTrace(
        run_id=run_id,
        config=config,
        identity=identity,
        state=state,
        results=tuple(context.results),
    )


def _logged_steps(
    steps: Sequence[BacktestStep],
    *,
    logger: logging.Logger,
    log_context: RunLogContext,
) -> list[BacktestStep]:
    """Wrap each pipeline step to emit stage-correlated log events (SP 2.71).

    Each stage emits ``backtest_stage_started`` and ``backtest_stage_completed``
    events (or ``backtest_stage_failed`` on error) carrying the run correlation
    fields narrowed to that stage. The wrapper preserves the original handler's
    warnings and re-raises exceptions for the engine to handle.
    """
    wrapped: list[BacktestStep] = []
    for step in steps:
        inner = step.run
        stage = step.stage

        def run(
            _inner: Callable[[], Sequence[str]] = inner,
            _stage: BacktestStage = stage,
        ) -> Sequence[str]:
            stage_context = log_context.with_stage(_stage)
            log_run_event(logger, context=stage_context, event="backtest_stage_started")
            try:
                warnings = tuple(_inner())
            except Exception:
                log_run_event(
                    logger,
                    context=stage_context,
                    event="backtest_stage_failed",
                    level=logging.ERROR,
                )
                raise
            log_run_event(
                logger,
                context=stage_context,
                event="backtest_stage_completed",
                warnings=list(warnings),
            )
            return warnings

        wrapped.append(BacktestStep(step.stage, run))
    return wrapped
