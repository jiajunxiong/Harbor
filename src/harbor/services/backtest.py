"""Backtest run, status and report service (MVP 2 / SP 2.67–2.69).

Orchestrates a CLI backtest run (SP 2.67): loads the versioned strategy
configuration (SP 2.5), computes the run identity (SP 2.48), creates the run
master record (SP 2.6), executes the SP 2.47 pipeline over a universe
assembled from the storage reader (SP 2.51), persists the day-by-day results
(SP 2.7) and updates the run status (SP 2.6), returning the run id and status.

Also renders a run's status view (SP 2.68) and its research report in
JSON/CSV/HTML (SP 2.69): the report reassembles an SP 2.58-shaped artifact
from the persisted result rows and the run's configuration snapshot, so the
three export formats work on exactly what the database can honestly reproduce.

The service lives in the orchestration layer: it composes core domain logic
with the storage repositories and reader, so the CLI command stays thin and
free of business logic. A documented default selection — hold every active
stock-pool member at equal weight on each rebalance day — is used so a CLI run
is a genuine, reconcilable backtest; the factor-based selection pipeline
(SP 2.15–2.28) is a separate concern not wired here.
"""

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any, Protocol

from sqlalchemy.engine import Connection

from harbor.core.audit_query import RunAudit, RunRecord, build_run_audit
from harbor.core.backtest_config import BacktestConfig
from harbor.core.backtest_config_loader import config_hash, load_backtest_config
from harbor.core.backtest_domain import BacktestStatus, Currency, Market, to_market_target
from harbor.core.backtest_interfaces import DailyQuote, Dividend, TradingCalendar
from harbor.core.backtest_runner import BacktestTrace, MockUniverse, run_end_to_end_backtest
from harbor.core.csv_export import export_all_csvs
from harbor.core.equity import EntitlementEvent
from harbor.core.html_report import render_html_report
from harbor.core.market_registry import get_market_config
from harbor.core.rebalance_schedule import RebalanceSchedule, generate_rebalance_days
from harbor.core.resume_policy import ResumeAction, can_cancel, decide_resume
from harbor.core.run_identity import identity_from_config
from harbor.core.run_logging import RunLogContext, log_run_event
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


def _single_market(config: BacktestConfig) -> Market | None:
    """Return the sole configured market, or None for a cross-market run.

    A single-market run can correlate its logs to one market; a cross-market
    run spans several so the market field stays None (SP 2.71).
    """
    markets = tuple(dict.fromkeys(quota.market for quota in config.market_quotas))
    return markets[0] if len(markets) == 1 else None


def run_backtest_command(
    *,
    config_path: str,
    code_version: str,
    data_cutoff: date | None,
    repository: BacktestRepository,
    universe: MockUniverse,
    resume_of: str | None = None,
    logger: logging.Logger | None = None,
) -> BacktestCommandResult:
    """Run a backtest from a config file and persist the run and its results.

    Args:
        config_path: Path to the versioned strategy configuration (SP 2.5).
        code_version: The code version recorded with the run (SP 2.48).
        data_cutoff: The data cutoff date; defaults to the config end date.
        repository: The SP 2.6 backtest repository.
        universe: The fixed market data the run executes over (SP 2.51).
        resume_of: Optional original run id this run resumes from (SP 2.70);
            the new run always gets its own fresh ``run_id``.
        logger: Optional structured logger (SP 2.71); when provided the run
            emits ``backtest_run_*`` lifecycle events and per-stage events
            carrying ``backtest_run_id``/``strategy_version``/``market``/
            ``stage``. Config values are never logged.

    Returns:
        The run id and final status (COMPLETED or FAILED).

    Raises:
        BacktestServiceError: If the configuration cannot be loaded.
    """
    try:
        config = load_backtest_config(config_path)
    except (OSError, ValueError) as error:
        raise BacktestServiceError(f"Cannot load backtest config: {error}.") from error
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
        resume_of=resume_of,
    )
    log_context = RunLogContext(
        run_id=run_id,
        strategy_version=config.strategy_version,
        market=_single_market(config),
    )
    if logger is not None:
        log_run_event(logger, context=log_context, event="backtest_run_started")
    finished_at = datetime.now(timezone.utc)
    try:
        trace = run_end_to_end_backtest(
            run_id=run_id,
            config=config,
            universe=universe,
            data_cutoff=cutoff,
            code_version=code_version,
            log_context=log_context if logger is not None else None,
            logger=logger,
        )
    except Exception as error:  # isolation point: record the failure, never crash
        if logger is not None:
            log_run_event(
                logger,
                context=log_context,
                event="backtest_run_failed",
                level=logging.ERROR,
                error_summary=str(error),
            )
        repository.update_run(
            run_id=run_id,
            status=BacktestStatus.FAILED.value,
            finished_at=finished_at,
            error_summary=str(error),
        )
        return BacktestCommandResult(run_id=run_id, status=BacktestStatus.FAILED)
    if not trace.succeeded:
        summary = trace.state.diagnostics.error_summary or "backtest failed without a diagnostic"
        if logger is not None:
            log_run_event(
                logger,
                context=log_context,
                event="backtest_run_failed",
                level=logging.ERROR,
                error_summary=summary,
            )
        repository.update_run(
            run_id=run_id,
            status=BacktestStatus.FAILED.value,
            finished_at=finished_at,
            error_summary=summary,
        )
        return BacktestCommandResult(run_id=run_id, status=BacktestStatus.FAILED)
    _persist_results(repository, trace)
    if logger is not None:
        log_run_event(logger, context=log_context, event="backtest_run_completed")
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
    logger: logging.Logger | None = None,
) -> BacktestCommandResult:
    """Run a CLI backtest from a config file against the database (SP 2.67).

    Loads the configuration, assembles the universe from the storage reader
    (pool-based selections at each rebalance day), executes the run and
    persists the results within the caller's transaction. When ``logger`` is
    supplied, the run emits structured correlation events (SP 2.71).
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
        logger=logger,
    )


class BacktestCancelError(ValueError):
    """Raised when a backtest run cannot be cancelled (SP 2.70)."""


class BacktestResumeError(ValueError):
    """Raised when a backtest run cannot be resumed (SP 2.70)."""


def cancel_backtest(*, connection: Connection, run_id: str) -> BacktestCommandResult:
    """Cancel a run that is still initializing or running (SP 2.70).

    Only runs in INITIALIZING or RUNNING may be cancelled (SP 2.46 state
    machine); terminal runs are left untouched. Returns the run id and the
    CANCELLED status on success.

    Raises:
        BacktestCancelError: If the run does not exist or cannot be cancelled.
    """
    repository = BacktestRepository(connection)
    run_rows = [dict(row) for row in connection.execute(repository.get_run(run_id)).mappings()]
    if not run_rows:
        raise BacktestCancelError(f"No backtest run found for run id {run_id!r}.")
    status = BacktestStatus(run_rows[0]["status"])
    if not can_cancel(status):
        raise BacktestCancelError(f"Backtest run {run_id!r} ({status.value}) cannot be cancelled.")
    repository.update_run(
        run_id=run_id,
        status=BacktestStatus.CANCELLED.value,
        finished_at=datetime.now(timezone.utc),
    )
    return BacktestCommandResult(run_id=run_id, status=BacktestStatus.CANCELLED)


def resume_backtest_from_config(
    *,
    config_path: str,
    code_version: str,
    data_cutoff: date | None,
    connection: Connection,
    original_run_id: str,
    logger: logging.Logger | None = None,
) -> BacktestCommandResult:
    """Resume a failed or cancelled run as a new run linked to the original.

    The resume policy (SP 2.70) is applied: runs still in progress are
    rejected, completed runs are reused (SP 2.48) and only failed/cancelled
    runs produce a new run, linked back via ``resume_of``. The resumed config
    must hash to the original run's config hash. When ``logger`` is supplied,
    the resumed run emits structured correlation events (SP 2.71).

    Raises:
        BacktestResumeError: If the run is missing, not resumable or the
            config does not match the original run.
    """
    config = load_backtest_config(config_path)
    repository = BacktestRepository(connection)
    run_rows = [
        dict(row) for row in connection.execute(repository.get_run(original_run_id)).mappings()
    ]
    if not run_rows:
        raise BacktestResumeError(f"No backtest run found for run id {original_run_id!r}.")
    status = BacktestStatus(run_rows[0]["status"])
    decision = decide_resume(run_id=original_run_id, status=status)
    if decision.action is not ResumeAction.NEW_RUN:
        raise BacktestResumeError(decision.reason)
    if config_hash(config) != run_rows[0]["config_hash"]:
        raise BacktestResumeError(
            "Resume config does not match the original run's configuration hash; "
            f"refusing to link a new run to {original_run_id!r}."
        )
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
        resume_of=original_run_id,
        logger=logger,
    )


class BacktestShowError(ValueError):
    """Raised when a run's status view cannot be assembled (SP 2.68)."""


@dataclass(frozen=True)
class BacktestShowResult:
    """A run's status view: audit (SP 2.66) plus core metrics (SP 2.68)."""

    audit: RunAudit
    day_count: int
    net_value_first: float | None
    net_value_last: float | None
    cumulative_return: float | None
    metrics: MappingProxyType[str, float]

    def to_dict(self) -> dict[str, Any]:
        """Render the status view as a JSON-safe dict."""
        audit = self.audit
        return {
            "run_id": audit.run_id,
            "status": audit.status.value,
            "strategy": audit.strategy,
            "strategy_version": audit.strategy_version,
            "code_version": audit.code_version,
            "config_hash": audit.config_hash,
            "markets": [market.value for market in audit.markets],
            "data_range": {
                "start": audit.start_date.isoformat() if audit.start_date is not None else None,
                "end": audit.end_date.isoformat() if audit.end_date is not None else None,
                "cutoff": audit.data_cutoff.isoformat(),
            },
            "base_currency": audit.base_currency.value if audit.base_currency is not None else None,
            "initial_capital": audit.initial_capital,
            "day_count": self.day_count,
            "net_value_first": self.net_value_first,
            "net_value_last": self.net_value_last,
            "cumulative_return": self.cumulative_return,
            "metrics": dict(self.metrics),
            "failure_reason": audit.failure_reason,
        }

    def readable(self) -> str:
        """Render the status view as a human-readable research summary."""
        audit = self.audit
        markets = ", ".join(market.value for market in audit.markets) or "—"
        start = audit.start_date.isoformat() if audit.start_date is not None else "—"
        end = audit.end_date.isoformat() if audit.end_date is not None else "—"
        base = audit.base_currency.value if audit.base_currency is not None else "—"
        initial = "—" if audit.initial_capital is None else f"{audit.initial_capital:,.2f}"
        first = "—" if self.net_value_first is None else f"{self.net_value_first:,.2f}"
        last = "—" if self.net_value_last is None else f"{self.net_value_last:,.2f}"
        cumulative = "—" if self.cumulative_return is None else f"{self.cumulative_return:.2%}"
        lines = [
            f"Backtest run {audit.run_id}:",
            f"  status: {audit.status.value}",
            f"  strategy: {audit.strategy} v{audit.strategy_version}",
            f"  code version: {audit.code_version}",
            f"  config hash: {audit.config_hash}",
            f"  markets: {markets}",
            f"  data range: {start} -> {end} (cutoff {audit.data_cutoff.isoformat()})",
            f"  base currency: {base}",
            f"  initial capital: {initial}",
            f"  day count: {self.day_count}",
            f"  net value: {first} -> {last} (cumulative {cumulative})",
        ]
        if self.metrics:
            rendered = ", ".join(
                f"{key}={value:.4f}" for key, value in sorted(self.metrics.items())
            )
            lines.append(f"  metrics: {rendered}")
        reason = audit.failure_reason
        if reason is not None:
            lines.append(f"  failure reason: {reason}")
        return "\n".join(lines)


def show_backtest(*, connection: Connection, run_id: str) -> BacktestShowResult:
    """Assemble the status view for a run id (SP 2.68).

    Args:
        connection: The database connection.
        run_id: The backtest run id.

    Returns:
        A :class:`BacktestShowResult` with the audit and core metrics.

    Raises:
        BacktestShowError: If no run exists for the id.
    """
    repository = BacktestRepository(connection)
    run_rows = [dict(row) for row in connection.execute(repository.get_run(run_id)).mappings()]
    if not run_rows:
        raise BacktestShowError(f"No backtest run found for run id {run_id!r}.")
    net_value_rows = [
        dict(row) for row in connection.execute(repository.list_net_values(run_id)).mappings()
    ]
    metric_rows = [
        dict(row) for row in connection.execute(repository.list_metrics(run_id)).mappings()
    ]
    return _show_backtest_from_rows(run_rows[0], net_value_rows, metric_rows)


def _show_backtest_from_rows(
    run_row: Mapping[str, Any],
    net_value_rows: Sequence[Mapping[str, Any]],
    metric_rows: Sequence[Mapping[str, Any]],
) -> BacktestShowResult:
    """Assemble a status view from the fetched run, net-value and metric rows."""
    record = RunRecord(
        run_id=run_row["run_id"],
        config_hash=run_row["config_hash"],
        config_snapshot=dict(run_row["config_snapshot"]),
        strategy=run_row["strategy"],
        strategy_version=run_row["strategy_version"],
        code_version=run_row["code_version"],
        data_cutoff=run_row["data_cutoff"],
        status=BacktestStatus(run_row["status"]),
        started_at=run_row["started_at"],
        finished_at=run_row["finished_at"],
        error_summary=run_row["error_summary"],
    )
    audit = build_run_audit(record)
    values = [float(row["total_value"]) for row in net_value_rows]
    first = values[0] if values else None
    last = values[-1] if values else None
    cumulative = None
    if first is not None and last is not None and first > 0:
        cumulative = last / first - 1.0
    metrics = MappingProxyType(
        {str(row["metric_name"]): float(row["value"]) for row in metric_rows}
    )
    return BacktestShowResult(
        audit=audit,
        day_count=len(values),
        net_value_first=first,
        net_value_last=last,
        cumulative_return=cumulative,
        metrics=metrics,
    )


class BacktestReportError(ValueError):
    """Raised when a run's report cannot be rendered (SP 2.69)."""


_REPORT_FORMATS = ("json", "csv", "html")


def build_report_artifact(*, connection: Connection, run_id: str) -> dict[str, Any]:
    """Reassemble an SP 2.58-shaped artifact from the persisted rows (SP 2.69).

    Only the sections the database can honestly reproduce are populated: net
    values, positions, trades (fills) and refused orders are read from the
    SP 2.7 result tables; dividends, corporate actions and warnings have no
    persisted table so they are empty; the metrics sections are ``None`` (they
    are not recomputed here). Unavailable fields within a row are ``None``
    rather than fabricated.

    Raises:
        BacktestReportError: If no run exists for the id.
    """
    repository = BacktestRepository(connection)
    run_rows = [dict(row) for row in connection.execute(repository.get_run(run_id)).mappings()]
    if not run_rows:
        raise BacktestReportError(f"No backtest run found for run id {run_id!r}.")
    run_row = run_rows[0]
    markets = [str(market) for market in dict(run_row["config_snapshot"]).get("markets", ())]
    net_value_rows = [
        dict(row) for row in connection.execute(repository.list_net_values(run_id)).mappings()
    ]
    metric_rows = [
        dict(row) for row in connection.execute(repository.list_metrics(run_id)).mappings()
    ]
    position_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    refused_rows: list[dict[str, Any]] = []
    for market in markets:
        position_rows.extend(
            dict(row)
            for row in connection.execute(repository.list_positions(market, run_id)).mappings()
        )
        fill_rows.extend(
            dict(row)
            for row in connection.execute(repository.list_fills(market, run_id)).mappings()
        )
        refused_rows.extend(
            dict(row)
            for row in connection.execute(
                repository.list_rejected_trades(market, run_id)
            ).mappings()
        )
    return _artifact_from_rows(
        run_row, net_value_rows, position_rows, fill_rows, refused_rows, metric_rows
    )


def _artifact_from_rows(
    run_row: Mapping[str, Any],
    net_value_rows: Sequence[Mapping[str, Any]],
    position_rows: Sequence[Mapping[str, Any]],
    fill_rows: Sequence[Mapping[str, Any]],
    refused_rows: Sequence[Mapping[str, Any]],
    metric_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the SP 2.58-shaped artifact from the fetched result rows."""
    config = dict(run_row["config_snapshot"])
    net_values = [
        {
            "date": str(row["as_of_date"]),
            "currency": str(row["currency"]),
            "cash": float(row["cash"]),
            "securities_value": float(row["securities_value"]),
            "fees_paid": float(row["fees_paid"]),
            "total_value": float(row["total_value"]),
            "fx_pnl": None,
        }
        for row in net_value_rows
    ]
    positions = [
        {
            "date": str(row["as_of_date"]),
            "market": str(row["market"]),
            "symbol": str(row["symbol"]),
            "quantity": float(row["quantity"]),
            "currency": str(row["currency"]),
        }
        for row in position_rows
    ]
    trades = [
        {
            "date": str(row["trade_date"]),
            "order_ref": str(row["order_ref"]),
            "market": str(row["market"]),
            "symbol": str(row["symbol"]),
            "side": str(row["side"]),
            "quantity": float(row["quantity"]),
            "price": float(row["price"]),
            "currency": str(row["currency"]),
            "fee": float(row["fee"]),
            "notional": float(row["quantity"]) * float(row["price"]),
        }
        for row in fill_rows
    ]
    refused = [
        {
            "date": None,
            "market": str(row["market"]),
            "symbol": str(row["symbol"]),
            "side": str(row["side"]) if row.get("side") is not None else None,
            "quantity": float(row["quantity"]) if row.get("quantity") is not None else None,
            "reason": str(row["reason"]),
        }
        for row in refused_rows
    ]
    first_date = net_values[0]["date"] if net_values else config.get("start_date")
    last_date = net_values[-1]["date"] if net_values else config.get("end_date")
    return {
        "schema_version": "1.0",
        "run": {
            "run_id": str(run_row["run_id"]),
            "status": str(run_row["status"]),
            "succeeded": str(run_row["status"]) == BacktestStatus.COMPLETED.value,
            "inputs": {
                "code_version": str(run_row["code_version"]),
                "config_hash": str(run_row["config_hash"]),
                "data_cutoff": str(run_row["data_cutoff"]),
                "data_range_start": first_date,
                "data_range_end": last_date,
            },
            "base_currency": config.get("base_currency"),
            "initial_capital": config.get("initial_capital"),
            "day_count": len(net_values),
            "reconciliation_failures": [],
        },
        "config": config,
        "metrics": {
            "performance": None,
            "trade_stats": None,
            "exposure": None,
            "drawdown": None,
            "attribution": None,
        },
        "net_values": net_values,
        "positions": positions,
        "trades": trades,
        "dividends": [],
        "corporate_actions": [],
        "refused": refused,
        "warnings": [],
    }


def _render_artifact(artifact: dict[str, Any], report_format: str) -> str:
    """Render an artifact in the requested report format (SP 2.69)."""
    if report_format == "json":
        return json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False)
    if report_format == "csv":
        csvs = export_all_csvs(artifact)
        return "\n\n".join(f"# {name}\n{content.rstrip()}" for name, content in csvs.items())
    if report_format == "html":
        return render_html_report(artifact)
    raise BacktestReportError(
        f"Unknown report format {report_format!r}; expected one of {sorted(_REPORT_FORMATS)}."
    )


def report_backtest(*, connection: Connection, run_id: str, report_format: str) -> str:
    """Render a run's research report in the requested format (SP 2.69).

    Args:
        connection: The database connection.
        run_id: The backtest run id.
        report_format: One of ``json``, ``csv`` or ``html``.

    Returns:
        The rendered report document.

    Raises:
        BacktestReportError: If the run is missing or the format is unknown.
    """
    artifact = build_report_artifact(connection=connection, run_id=run_id)
    return _render_artifact(artifact, report_format)
