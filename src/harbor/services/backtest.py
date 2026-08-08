"""Backtest run service (MVP 2 / SP 2.67).

Orchestrates a CLI backtest run: loads the versioned strategy configuration
(SP 2.5), computes the run identity (SP 2.48), creates the run master record
(SP 2.6), executes the SP 2.47 pipeline over a universe assembled from the
storage reader (SP 2.51), persists the day-by-day results (SP 2.7) and updates
the run status (SP 2.6), returning the run id and status.

The service lives in the orchestration layer: it composes core domain logic
with the storage repositories and reader, so the CLI command stays thin and
free of business logic. A documented default selection — hold every active
stock-pool member at equal weight on each rebalance day — is used so a CLI run
is a genuine, reconcilable backtest; the factor-based selection pipeline
(SP 2.15–2.28) is a separate concern not wired here.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

from sqlalchemy.engine import Connection

from harbor.core.backtest_config import BacktestConfig
from harbor.core.backtest_config_loader import load_backtest_config
from harbor.core.backtest_domain import BacktestStatus, Currency, Market, to_market_target
from harbor.core.backtest_interfaces import DailyQuote, Dividend, TradingCalendar
from harbor.core.backtest_runner import BacktestTrace, MockUniverse, run_end_to_end_backtest
from harbor.core.equity import EntitlementEvent
from harbor.core.market_registry import get_market_config
from harbor.core.rebalance_schedule import RebalanceSchedule, generate_rebalance_days
from harbor.core.run_identity import identity_from_config
from harbor.core.stock_pool import StockPool
from harbor.core.trading_calendar import MarketTradingCalendar
from harbor.storage.backtest_data_reader import StorageBacktestDataReader
from harbor.storage.backtest_repositories import BacktestRepository


class BacktestServiceError(ValueError):
    """Raised when a backtest run cannot be orchestrated (SP 2.67)."""


class BacktestUniverseReader(Protocol):
    """The subset of the backtest data reader the universe assembly needs."""

    def stock_pool(self, market: Market, as_of: date, *, historical_known: bool) -> StockPool: ...

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[DailyQuote]: ...

    def dividends(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[Dividend]: ...

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[EntitlementEvent]: ...

    def fx_rate(
        self, from_currency: Currency, to_currency: Currency, as_of: date
    ) -> float | None: ...


@dataclass(frozen=True)
class BacktestCommandResult:
    """The outcome of a CLI backtest run: run id and final status."""

    run_id: str
    status: BacktestStatus


def _quote_currency(market: Market) -> Currency:
    """Return the currency securities in ``market`` are quoted in."""
    return Currency(get_market_config(to_market_target(market)).currency)


def _run_markets(config: BacktestConfig) -> tuple[str, ...]:
    """Return the configured market values, deduplicated and in order."""
    return tuple(dict.fromkeys(quota.market.value for quota in config.market_quotas))


def run_backtest_command(
    *,
    config_path: str,
    code_version: str,
    data_cutoff: date | None,
    repository: BacktestRepository,
    universe: MockUniverse,
) -> BacktestCommandResult:
    """Run a backtest from a config file and persist the run and its results.

    Args:
        config_path: Path to the versioned strategy configuration (SP 2.5).
        code_version: The code version recorded with the run (SP 2.48).
        data_cutoff: The data cutoff date; defaults to the config end date.
        repository: The SP 2.6 backtest repository.
        universe: The fixed market data the run executes over (SP 2.51).

    Returns:
        The run id and final status (COMPLETED or FAILED).

    Raises:
        BacktestServiceError: If the configuration cannot be loaded.
    """
    try:
        config = load_backtest_config(config_path)
    except (OSError, ValueError) as error:
        raise BacktestServiceError(f"Cannot load backtest config: {error}") from error
    cutoff = data_cutoff if data_cutoff is not None else config.end_date
    identity = identity_from_config(config=config, data_cutoff=cutoff, code_version=code_version)
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc)
    repository.create_run(
        run_id=run_id,
        config_hash=identity.config_hash,
        config_snapshot=config.model_dump(mode="json"),
        strategy=config.strategy,
        strategy_version=config.strategy_version,
        code_version=code_version,
        data_cutoff=cutoff,
        started_at=started_at,
        status=BacktestStatus.RUNNING.value,
    )
    finished_at = datetime.now(timezone.utc)
    try:
        trace = run_end_to_end_backtest(
            run_id=run_id,
            config=config,
            universe=universe,
            data_cutoff=cutoff,
            code_version=code_version,
        )
    except Exception as error:  # isolation point: record the failure, never crash
        repository.update_run(
            run_id=run_id,
            status=BacktestStatus.FAILED.value,
            finished_at=finished_at,
            error_summary=str(error),
        )
        return BacktestCommandResult(run_id=run_id, status=BacktestStatus.FAILED)
    if not trace.succeeded:
        summary = trace.state.diagnostics.error_summary or "backtest failed without a diagnostic"
        repository.update_run(
            run_id=run_id,
            status=BacktestStatus.FAILED.value,
            finished_at=finished_at,
            error_summary=summary,
        )
        return BacktestCommandResult(run_id=run_id, status=BacktestStatus.FAILED)
    _persist_results(repository, trace)
    repository.update_run(
        run_id=run_id,
        status=BacktestStatus.COMPLETED.value,
        finished_at=finished_at,
    )
    return BacktestCommandResult(run_id=run_id, status=BacktestStatus.COMPLETED)


def _persist_results(repository: BacktestRepository, trace: BacktestTrace) -> None:
    """Persist the run's net values, fills and rejected trades (SP 2.7).

    Positions, rebalances and metrics are persisted by the report/export
    wiring of later SPs; the rows recorded here are exactly what the day-by-day
    trace already exposes without fabricating cost-basis data.
    """
    net_value_rows: list[dict[str, object]] = []
    fill_rows: list[dict[str, object]] = []
    rejected_rows: list[dict[str, object]] = []
    for result in trace.results:
        net = result.valuation.net_value
        net_value_rows.append(
            {
                "as_of_date": result.as_of,
                "currency": net.currency.value,
                "cash": net.cash,
                "securities_value": net.securities_value,
                "fees_paid": net.fees_paid,
                "total_value": net.total_value,
            }
        )
        for fill in result.fills:
            fill_rows.append(
                {
                    "trade_date": fill.trade_date,
                    "market": fill.market.value,
                    "symbol": fill.symbol,
                    "side": fill.side.value,
                    "quantity": fill.quantity,
                    "price": fill.price,
                    "fee": fill.fee,
                    "currency": fill.currency.value,
                    "order_ref": fill.order_ref,
                }
            )
        for refused in result.refused:
            order = refused.order
            rejected_rows.append(
                {
                    "market": order.market.value,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": order.quantity,
                    "reason": refused.reason,
                    "order_ref": order.ref,
                }
            )
    repository.insert_net_values(trace.run_id, net_value_rows)
    for market in _run_markets(trace.config):
        repository.insert_fills(
            market,
            trace.run_id,
            [row for row in fill_rows if row["market"] == market],
        )
        repository.insert_rejected_trades(
            market,
            trace.run_id,
            [row for row in rejected_rows if row["market"] == market],
        )


def build_universe(
    *,
    reader: BacktestUniverseReader,
    calendar: TradingCalendar,
    config: BacktestConfig,
    selections: Mapping[tuple[Market, date], tuple[str, ...]] | None = None,
    fx_rates: Mapping[tuple[Currency, Currency], Mapping[date, float]] | None = None,
) -> MockUniverse:
    """Assemble a :class:`MockUniverse` from the storage reader (SP 2.51).

    Quotes, dividends and corporate actions are read for every symbol of each
    configured market's stock pool on the configuration start date; FX rates
    and the per-rebalance selection snapshots are supplied by the caller.
    """
    quotes: dict[tuple[Market, str], dict[date, DailyQuote]] = {}
    dividends: dict[tuple[Market, str], tuple[Dividend, ...]] = {}
    corporate_actions: dict[tuple[Market, str], tuple[EntitlementEvent, ...]] = {}
    for quota in config.market_quotas:
        market = quota.market
        pool = reader.stock_pool(market, config.start_date, historical_known=True)
        for symbol in pool.symbols:
            quotes[(market, symbol)] = {
                quote.day: quote
                for quote in reader.daily_quotes(market, symbol, config.start_date, config.end_date)
            }
            dividends[(market, symbol)] = tuple(
                reader.dividends(market, symbol, config.start_date, config.end_date)
            )
            corporate_actions[(market, symbol)] = tuple(
                reader.corporate_actions(market, symbol, config.start_date, config.end_date)
            )
    return MockUniverse(
        calendar=calendar,
        quotes=quotes,
        dividends=dividends,
        corporate_actions=corporate_actions,
        fx_rates=fx_rates if fx_rates is not None else {},
        selections=selections if selections is not None else {},
    )


def pool_selections(
    *,
    reader: BacktestUniverseReader,
    calendar: TradingCalendar,
    config: BacktestConfig,
    schedule: RebalanceSchedule | None = None,
) -> dict[tuple[Market, date], tuple[str, ...]]:
    """Select the active stock pool at each market's rebalance day (SP 2.67).

    A documented default selection for the CLI: every active pool member is
    held at equal weight on each rebalance day. The factor-based selection
    (SP 2.15–2.28) is a separate concern and not wired here.
    """
    effective = schedule if schedule is not None else RebalanceSchedule()
    result: dict[tuple[Market, date], tuple[str, ...]] = {}
    for quota in config.market_quotas:
        market = quota.market
        days = generate_rebalance_days(
            market, config.start_date, config.end_date, calendar, effective
        )
        for day in days:
            pool = reader.stock_pool(market, day, historical_known=True)
            result[(market, day)] = pool.symbols
    return result


def _build_fx_rates(
    reader: BacktestUniverseReader,
    config: BacktestConfig,
    calendar: TradingCalendar,
) -> dict[tuple[Currency, Currency], dict[date, float]]:
    """Build the day-level FX rates the runner needs for cross-market legs."""
    base = config.base_currency
    rates: dict[tuple[Currency, Currency], dict[date, float]] = {}
    for quota in config.market_quotas:
        quote = _quote_currency(quota.market)
        if quote is base:
            continue
        day_rates: dict[date, float] = {}
        for day in calendar.trading_days(quota.market, config.start_date, config.end_date):
            rate = reader.fx_rate(quote, base, day)
            if rate is not None and rate > 0:
                day_rates[day] = rate
        rates[(quote, base)] = day_rates
    return rates


def run_backtest_from_config(
    *,
    config_path: str,
    code_version: str,
    data_cutoff: date | None,
    connection: Connection,
) -> BacktestCommandResult:
    """Run a CLI backtest from a config file against the database (SP 2.67).

    Loads the configuration, assembles the universe from the storage reader
    (pool-based selections at each rebalance day), executes the run and
    persists the results within the caller's transaction.
    """
    config = load_backtest_config(config_path)
    repository = BacktestRepository(connection)
    reader = StorageBacktestDataReader(connection)
    calendar = MarketTradingCalendar()
    selections = pool_selections(reader=reader, calendar=calendar, config=config)
    universe = build_universe(
        reader=reader,
        calendar=calendar,
        config=config,
        selections=selections,
        fx_rates=_build_fx_rates(reader, config, calendar),
    )
    return run_backtest_command(
        config_path=config_path,
        code_version=code_version,
        data_cutoff=data_cutoff,
        repository=repository,
        universe=universe,
    )
