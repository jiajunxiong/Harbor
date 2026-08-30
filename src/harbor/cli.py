"""Command-line entry points for Harbor."""

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from harbor import __version__
from harbor.config import MarketTarget, Settings
from harbor.core.ingestion import (
    CorporateActionIngestor,
    DailyQuoteIngestor,
    DividendIngestor,
    FinancialIngestor,
    SecuritiesIngestor,
)
from harbor.core.interfaces import Capability, MarketDataProvider
from harbor.core.paper_domain import ApprovalDecision
from harbor.core.quality_report import render_quality_csv, summarize_quality_issues
from harbor.infrastructure.data_providers.factory import (
    create_provider,
    print_capability_report,
)
from harbor.logging import configure_logging, get_logger
from harbor.services.backtest import (
    cancel_backtest,
    report_backtest,
    resume_backtest_from_config,
    run_backtest_from_config,
    show_backtest,
)
from harbor.services.paper import (
    PaperRepositoryStore,
    paper_approve_order_command,
    paper_init_command,
    paper_order_list_command,
    paper_order_show_command,
    paper_reconcile_command,
    paper_report_command,
    paper_signal_command,
    paper_start_command,
    paper_status_command,
    paper_stop_command,
)
from harbor.services.validation import (
    report_validation,
    run_validation_command,
    run_validation_from_config,
    show_validation,
)
from harbor.storage.repositories import Repository


def build_parser() -> argparse.ArgumentParser:
    """Build the Harbor command-line parser."""
    parser = argparse.ArgumentParser(prog="harbor-cli", description="Harbor market-data tools")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("config", help="Show the active non-secret configuration.")
    subparsers.add_parser("providers", help="Show the data provider capability report.")
    fetch_parser = subparsers.add_parser(
        "fetch", help="Fetch market data from the configured provider."
    )
    fetch_subparsers = fetch_parser.add_subparsers(dest="fetch_command", required=True)
    securities_parser = fetch_subparsers.add_parser(
        "securities", help="Fetch the securities universe for a market."
    )
    securities_parser.add_argument(
        "--market", type=MarketTarget, required=True, help="Market to fetch (HK or US)."
    )
    daily_parser = fetch_subparsers.add_parser(
        "daily", help="Fetch daily quotes for a symbol, or for all symbols with --all."
    )
    daily_parser.add_argument(
        "--market", type=MarketTarget, required=True, help="Market to fetch (HK or US)."
    )
    daily_parser.add_argument(
        "--symbol", required=False, help="Security symbol (required unless --all is given)."
    )
    daily_parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch daily quotes for every registered security in the market.",
    )
    daily_parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Seconds between symbols when --all is used (rate limiting).",
    )
    daily_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max symbols to fetch when --all is used (0 = all).",
    )
    daily_parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=None,
        help="Start date (ISO), defaults to five years before the end date.",
    )
    daily_parser.add_argument(
        "--end", type=date.fromisoformat, default=None, help="End date (ISO), defaults to today."
    )
    all_parser = fetch_subparsers.add_parser(
        "all", help="Fetch every supported dataset for a market."
    )
    all_parser.add_argument(
        "--market", type=MarketTarget, required=True, help="Market to fetch (HK or US)."
    )
    all_parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=None,
        help="Start date (ISO), defaults to five years before the end date.",
    )
    all_parser.add_argument(
        "--end", type=date.fromisoformat, default=None, help="End date (ISO), defaults to today."
    )
    quality_parser = subparsers.add_parser("quality", help="Inspect data-quality results.")
    quality_subparsers = quality_parser.add_subparsers(dest="quality_command", required=True)
    report_parser = quality_subparsers.add_parser(
        "report", help="Show a data-quality summary for a market."
    )
    report_parser.add_argument(
        "--market", type=MarketTarget, required=True, help="Market to report (HK or US)."
    )
    report_parser.add_argument(
        "--csv", default=None, help="Optional path to export the quality issues as CSV."
    )
    backtest_parser = subparsers.add_parser(
        "backtest", help="Run and inspect backtest research runs."
    )
    backtest_subparsers = backtest_parser.add_subparsers(dest="backtest_command", required=True)
    run_parser = backtest_subparsers.add_parser(
        "run", help="Run a backtest from a versioned strategy config file."
    )
    run_parser.add_argument(
        "--config", required=True, help="Path to the strategy configuration (YAML/JSON)."
    )
    run_parser.add_argument(
        "--code-version",
        default=__version__,
        help="Code version recorded with the run; defaults to the package version.",
    )
    run_parser.add_argument(
        "--data-cutoff",
        type=date.fromisoformat,
        default=None,
        help="Data cutoff date (ISO); defaults to the config end date.",
    )
    show_parser = backtest_subparsers.add_parser(
        "show", help="Show a backtest run's config, data range, status and core metrics."
    )
    show_parser.add_argument("run_id", help="The backtest run id.")
    report_parser = backtest_subparsers.add_parser(
        "report", help="Render a backtest run's report as JSON, CSV or HTML."
    )
    report_parser.add_argument("run_id", help="The backtest run id.")
    report_parser.add_argument(
        "--format",
        choices=("json", "csv", "html"),
        default="json",
        help="Report format; defaults to json.",
    )
    cancel_parser = backtest_subparsers.add_parser(
        "cancel", help="Cancel a backtest run that is still initializing or running."
    )
    cancel_parser.add_argument("run_id", help="The backtest run id.")
    resume_parser = backtest_subparsers.add_parser(
        "resume",
        help="Resume a failed or cancelled run as a new run linked to the original.",
    )
    resume_parser.add_argument(
        "--config", required=True, help="Path to the strategy configuration (YAML/JSON)."
    )
    resume_parser.add_argument(
        "--resume-of", required=True, help="The original backtest run id to link the resume to."
    )
    resume_parser.add_argument(
        "--code-version",
        default=__version__,
        help="Code version recorded with the run; defaults to the package version.",
    )
    resume_parser.add_argument(
        "--data-cutoff",
        type=date.fromisoformat,
        default=None,
        help="Data cutoff date (ISO); defaults to the config end date.",
    )
    validation_parser = subparsers.add_parser(
        "validation", help="Create and inspect out-of-sample validation runs."
    )
    validation_subparsers = validation_parser.add_subparsers(
        dest="validation_command", required=True
    )
    validation_run_parser = validation_subparsers.add_parser(
        "run", help="Create a DRAFT validation run from a versioned config file."
    )
    validation_run_parser.add_argument(
        "--config", required=True, help="Path to the validation configuration (YAML/JSON)."
    )
    freeze_parser = validation_subparsers.add_parser(
        "freeze", help="Freeze the dataset, calendar and split (DRAFT -> DATA_FROZEN)."
    )
    freeze_parser.add_argument("run_id", help="The validation run id.")
    tune_parser = validation_subparsers.add_parser(
        "tune", help="Begin parameter tuning (DATA_FROZEN -> TUNING)."
    )
    tune_parser.add_argument("run_id", help="The validation run id.")
    lock_parser = validation_subparsers.add_parser(
        "lock", help="Lock the independent test set (DATA_FROZEN/TUNING -> TEST_LOCKED)."
    )
    lock_parser.add_argument("run_id", help="The validation run id.")
    evaluate_parser = validation_subparsers.add_parser(
        "evaluate", help="Evaluate the independent holdout (TEST_LOCKED -> EVALUATED)."
    )
    evaluate_parser.add_argument("run_id", help="The validation run id.")
    show_parser = validation_subparsers.add_parser(
        "show", help="Show a validation run's status, split and artifact counts."
    )
    show_parser.add_argument("run_id", help="The validation run id.")
    report_parser = validation_subparsers.add_parser(
        "report", help="Render a validation run's report as JSON, CSV or HTML."
    )
    report_parser.add_argument("run_id", help="The validation run id.")
    report_parser.add_argument(
        "--format",
        choices=("json", "csv", "html"),
        default="json",
        help="Report format; defaults to json.",
    )
    paper_parser = subparsers.add_parser(
        "paper", help="Create, run and reconcile local paper-trading loops."
    )
    paper_subparsers = paper_parser.add_subparsers(dest="paper_command", required=True)
    init_parser = paper_subparsers.add_parser(
        "init", help="Create a DRAFT paper run from a versioned paper config file."
    )
    init_parser.add_argument(
        "--config", required=True, help="Path to the paper configuration (YAML/JSON)."
    )
    init_parser.add_argument(
        "--code-version",
        default=__version__,
        help="Code version recorded with the run; defaults to the package version.",
    )
    init_parser.add_argument(
        "--dataset-fingerprint",
        required=True,
        help="Dataset fingerprint recorded with the run (SP 4.9 replay identity).",
    )
    start_parser = paper_subparsers.add_parser(
        "start", help="Approve and activate a paper run (DRAFT -> APPROVED -> ACTIVE)."
    )
    start_parser.add_argument("run_id", help="The paper run id.")
    start_parser.add_argument(
        "--approver", default="system", help="The approver recorded with the run approval."
    )
    stop_parser = paper_subparsers.add_parser("stop", help="Stop a paper run (terminal state).")
    stop_parser.add_argument("run_id", help="The paper run id.")
    status_parser = paper_subparsers.add_parser(
        "status", help="Show a paper run's status and artifact counts."
    )
    status_parser.add_argument("run_id", help="The paper run id.")
    signal_parser = paper_subparsers.add_parser(
        "signal", help="Derive and persist paper orders from target weights (SP 4.85)."
    )
    signal_parser.add_argument("run_id", help="The paper run id.")
    signal_parser.add_argument(
        "--rebalance-date", type=date.fromisoformat, required=True, help="The rebalance date (ISO)."
    )
    signal_parser.add_argument(
        "--target",
        action="append",
        required=True,
        metavar="SYMBOL:WEIGHT",
        help="A target weight (repeatable), e.g. 0001.HK:0.5.",
    )
    signal_parser.add_argument(
        "--price",
        action="append",
        required=True,
        metavar="SYMBOL:PRICE",
        help="A reference price (repeatable), e.g. 0001.HK:50.0.",
    )
    signal_parser.add_argument(
        "--source-run-id", default=None, help="The OOS research run that produced the signal."
    )
    order_parser = paper_subparsers.add_parser("order", help="Inspect a paper run's orders.")
    order_subparsers = order_parser.add_subparsers(dest="order_command", required=True)
    order_list_parser = order_subparsers.add_parser("list", help="List a paper run's orders.")
    order_list_parser.add_argument("run_id", help="The paper run id.")
    order_show_parser = order_subparsers.add_parser("show", help="Show a single paper order.")
    order_show_parser.add_argument("run_id", help="The paper run id.")
    order_show_parser.add_argument("order_id", help="The order id.")
    approve_parser = paper_subparsers.add_parser(
        "approve", help="Approve a paper order or run (records an auditable approval)."
    )
    approve_parser.add_argument("run_id", help="The paper run id.")
    approve_parser.add_argument(
        "--order-id", default=None, help="The order id (when approving an order)."
    )
    approve_parser.add_argument("--approver", required=True, help="The approver.")
    approve_parser.add_argument("--reason", default=None, help="Optional approval reason.")
    reject_parser = paper_subparsers.add_parser(
        "reject", help="Reject a paper order (records an auditable rejection)."
    )
    reject_parser.add_argument("run_id", help="The paper run id.")
    reject_parser.add_argument("--order-id", required=True, help="The order id.")
    reject_parser.add_argument("--approver", required=True, help="The approver.")
    reject_parser.add_argument("--reason", default=None, help="Optional rejection reason.")
    reconcile_parser = paper_subparsers.add_parser(
        "reconcile", help="Reconcile a paper run's account against its net value (SP 4.87)."
    )
    reconcile_parser.add_argument("run_id", help="The paper run id.")
    reconcile_parser.add_argument(
        "--as-of", type=date.fromisoformat, required=True, help="The reconciliation date (ISO)."
    )
    paper_report_parser = paper_subparsers.add_parser(
        "report", help="Render a paper run's report as JSON, CSV or HTML."
    )
    paper_report_parser.add_argument("run_id", help="The paper run id.")
    paper_report_parser.add_argument(
        "--format",
        choices=("json", "csv", "html"),
        default="json",
        help="Report format; defaults to json.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run Harbor's command-line interface.

    Args:
        argv: Optional command arguments excluding the executable name.

    Returns:
        A process exit status.
    """
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "config":
        return _show_config(parser)
    if arguments.command == "providers":
        return _show_providers()
    if arguments.command == "fetch":
        return _show_fetch(parser, arguments)
    if arguments.command == "quality":
        return _show_quality(parser, arguments)
    if arguments.command == "backtest":
        return _show_backtest(parser, arguments)
    if arguments.command == "validation":
        return _show_validation(parser, arguments)
    if arguments.command == "paper":
        return _show_paper(parser, arguments)
    parser.error(f"Unsupported command: {arguments.command}")
    return 2


def _show_fetch(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Fetch market data and render a JSON summary."""
    if arguments.market not in (MarketTarget.HK, MarketTarget.US):
        parser.error("--market must be one of: HK, US")
        return 2
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    if arguments.fetch_command in ("securities", "daily", "all"):
        if arguments.fetch_command == "daily":
            if arguments.all and arguments.symbol:
                parser.error("--symbol and --all are mutually exclusive")
                return 2
            if not arguments.all and not arguments.symbol:
                parser.error("--all or --symbol is required for fetch daily")
                return 2
        try:
            if arguments.fetch_command == "securities":
                summary = _fetch_securities(arguments.market, settings)
            elif arguments.fetch_command == "daily":
                if arguments.all:
                    summary = _fetch_daily_all(
                        arguments.market,
                        settings,
                        arguments.start,
                        arguments.end,
                        arguments.limit,
                        arguments.delay,
                    )
                else:
                    summary = _fetch_daily(
                        arguments.market,
                        settings,
                        arguments.symbol,
                        arguments.start,
                        arguments.end,
                    )
            else:
                summary = _fetch_all(arguments.market, settings, arguments.start, arguments.end)
        except (NotImplementedError, ValueError) as error:
            parser.error(f"Fetch failed: {error}")
            return 2
        sys.stdout.write(f"{json.dumps(summary, sort_keys=True)}\n")
        return 0
    parser.error(f"Unsupported fetch command: {arguments.fetch_command}")
    return 2


def _provider_name(market: MarketTarget, settings: Settings) -> str:
    """Return the configured provider name for a market."""
    return settings.data_provider_hk if market is MarketTarget.HK else settings.data_provider_us


@contextmanager
def _repository_for(
    settings: Settings, market: MarketTarget
) -> Iterator[tuple[Repository, MarketDataProvider, str]]:
    """Yield a repository, provider, and run id for a market.

    The ingestion run is created before any data is written so that raw
    payloads can reference it, and the connection commits on success.
    """
    provider = create_provider(market, _provider_name(market, settings))
    run_id = uuid.uuid4().hex
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        repository = Repository(connection)
        repository.create_ingestion_run(
            market.value,
            run_id,
            _provider_name(market, settings),
            datetime.now(timezone.utc),
        )
        yield repository, provider, run_id


def _fetch_securities(market: MarketTarget, settings: Settings) -> dict[str, object]:
    """Fetch and store the securities universe for a market."""
    with _repository_for(settings, market) as bundle:
        repository, provider, run_id = bundle
        count = SecuritiesIngestor(repository, run_id=run_id).ingest(provider, market)
    return {
        "market": market.value,
        "provider": _provider_name(market, settings),
        "count": count,
    }


def _fetch_daily(
    market: MarketTarget,
    settings: Settings,
    symbol: str,
    start: date | None,
    end: date | None,
) -> dict[str, object]:
    """Fetch and store daily quotes for a symbol."""
    range_end = end if end is not None else date.today()
    range_start = start if start is not None else range_end - timedelta(days=365 * 5)
    with _repository_for(settings, market) as bundle:
        repository, provider, run_id = bundle
        count = DailyQuoteIngestor(repository, run_id=run_id).ingest(
            provider, market, symbol, range_start, range_end
        )
    return {
        "market": market.value,
        "symbol": symbol,
        "provider": _provider_name(market, settings),
        "count": count,
    }


def _fetch_daily_all(
    market: MarketTarget,
    settings: Settings,
    start: date | None,
    end: date | None,
    limit: int,
    delay: float,
) -> dict[str, object]:
    """Fetch daily quotes for every registered security in a market.

    Each symbol commits in its own transaction so a transient failure rolls
    back only that symbol; per-symbol failures are collected in the summary
    and never abort the batch. Progress is written to stderr so the JSON
    summary on stdout stays machine-readable. The writes are idempotent
    (``ON CONFLICT DO NOTHING``), so re-running is safe. ``limit`` caps the
    number of symbols processed (0 = all).
    """
    range_end = end if end is not None else date.today()
    range_start = start if start is not None else range_end - timedelta(days=365 * 5)
    provider_name = _provider_name(market, settings)
    engine = create_engine(settings.database_url)
    provider = create_provider(market, provider_name)
    with engine.connect() as connection:
        repository = Repository(connection)
        rows = connection.execute(repository.list_securities(market.value)).mappings()
        symbols = [str(row["symbol"]) for row in rows]
    if limit > 0:
        symbols = symbols[:limit]
    inserted = 0
    counts: dict[str, int] = {}
    failures: dict[str, str] = {}
    for symbol in symbols:
        try:
            with engine.begin() as connection:
                batch_repository = Repository(connection)
                run_id = uuid.uuid4().hex
                batch_repository.create_ingestion_run(
                    market.value,
                    run_id,
                    provider_name,
                    datetime.now(timezone.utc),
                )
                ingested = DailyQuoteIngestor(batch_repository, run_id=run_id).ingest(
                    provider, market, symbol, range_start, range_end
                )
            counts[symbol] = ingested
            inserted += ingested
        except Exception as error:  # noqa: BLE001 - one bad symbol must not abort the batch
            failures[symbol] = str(error)
        print(f"  {symbol}: {counts.get(symbol, 'FAILED')}", file=sys.stderr, flush=True)
        time.sleep(delay)
    return {
        "market": market.value,
        "provider": provider_name,
        "symbols": len(symbols),
        "count": inserted,
        "fetched": sum(1 for count in counts.values() if count > 0),
        "empty": sum(1 for count in counts.values() if count == 0),
        "failed": len(failures),
        "failures": failures,
    }


def _fetch_all(
    market: MarketTarget,
    settings: Settings,
    start: date | None,
    end: date | None,
) -> dict[str, object]:
    """Fetch every supported dataset for a market.

    The securities universe is ingested first, then each per-symbol dataset
    (daily quotes, dividends, financials, corporate actions) is ingested for
    every listed security, gated on the provider's declared capabilities.
    """
    range_end = end if end is not None else date.today()
    range_start = start if start is not None else range_end - timedelta(days=365 * 5)
    with _repository_for(settings, market) as bundle:
        repository, provider, run_id = bundle
        capabilities = provider.capabilities()
        symbols = [str(row["symbol"]) for row in provider.list_securities(market)]
        counts: dict[str, int] = {
            "securities": SecuritiesIngestor(repository, run_id=run_id).ingest(provider, market)
        }
        for symbol in symbols:
            if capabilities.supports(market, Capability.DAILY_QUOTES):
                ingested = DailyQuoteIngestor(repository, run_id=run_id).ingest(
                    provider, market, symbol, range_start, range_end
                )
                counts["daily_quotes"] = counts.get("daily_quotes", 0) + ingested
            if capabilities.supports(market, Capability.DIVIDENDS):
                ingested = DividendIngestor(repository, run_id=run_id).ingest(
                    provider, market, symbol, range_start, range_end
                )
                counts["dividends"] = counts.get("dividends", 0) + ingested
            if capabilities.supports(market, Capability.FUNDAMENTALS):
                ingested = FinancialIngestor(repository, run_id=run_id).ingest(
                    provider, market, symbol
                )
                counts["financials"] = counts.get("financials", 0) + ingested
            if capabilities.supports(market, Capability.CORPORATE_ACTIONS):
                ingested = CorporateActionIngestor(repository, run_id=run_id).ingest(
                    provider, market, symbol, range_start, range_end
                )
                counts["corporate_actions"] = counts.get("corporate_actions", 0) + ingested
    return {
        "market": market.value,
        "provider": _provider_name(market, settings),
        "counts": counts,
        "count": sum(counts.values()),
    }


def _show_quality(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Render a data-quality summary for a market and optionally export CSV."""
    if arguments.quality_command != "report":
        parser.error(f"Unsupported quality command: {arguments.quality_command}")
        return 2
    if arguments.market not in (MarketTarget.HK, MarketTarget.US):
        parser.error("--market must be one of: HK, US")
        return 2
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as connection:
            repository = Repository(connection)
            issues = repository.fetch_quality_issues(arguments.market.value)
        summary = summarize_quality_issues(arguments.market, issues)
        sys.stdout.write(f"{json.dumps(summary, sort_keys=True)}\n")
        if arguments.csv is not None:
            with open(arguments.csv, "w", encoding="utf-8") as handle:
                handle.write(render_quality_csv(issues))
        return 0
    except (OSError, ValueError) as error:
        parser.error(f"Quality report failed: {error}")
        return 2


def _show_providers() -> int:
    """Print the registered data provider capability report."""
    print_capability_report()
    return 0


def _show_backtest(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Dispatch the backtest subcommands."""
    if arguments.backtest_command == "run":
        return _show_backtest_run(parser, arguments)
    if arguments.backtest_command == "show":
        return _show_backtest_show(parser, arguments)
    if arguments.backtest_command == "report":
        return _show_backtest_report(parser, arguments)
    if arguments.backtest_command == "cancel":
        return _show_backtest_cancel(parser, arguments)
    if arguments.backtest_command == "resume":
        return _show_backtest_resume(parser, arguments)
    parser.error(f"Unsupported backtest command: {arguments.backtest_command}")
    return 2


def _show_backtest_run(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Run a backtest from a config file and render the run id and status."""
    if arguments.backtest_command != "run":
        parser.error(f"Unsupported backtest command: {arguments.backtest_command}")
        return 2
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    configure_logging(settings.log_level)
    logger = get_logger("backtest")
    try:
        engine = create_engine(settings.database_url)
        with engine.begin() as connection:
            result = run_backtest_from_config(
                config_path=arguments.config,
                code_version=arguments.code_version,
                data_cutoff=arguments.data_cutoff,
                connection=connection,
                logger=logger,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Backtest run failed: {error}")
        return 2
    summary = {"run_id": result.run_id, "status": result.status.value}
    sys.stdout.write(f"{json.dumps(summary, sort_keys=True)}\n")
    return 0


def _show_backtest_show(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Render a backtest run's config, data range, status and core metrics."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as connection:
            result = show_backtest(connection=connection, run_id=arguments.run_id)
    except (OSError, ValueError) as error:
        parser.error(f"Backtest show failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result.to_dict(), sort_keys=True)}\n")
    return 0


def _show_backtest_report(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Render a backtest run's report as JSON, CSV or HTML (SP 2.69)."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as connection:
            output = report_backtest(
                connection=connection,
                run_id=arguments.run_id,
                report_format=arguments.format,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Backtest report failed: {error}")
        return 2
    sys.stdout.write(output + "\n")
    return 0


def _show_backtest_cancel(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Cancel a backtest run that is still initializing or running (SP 2.70)."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    try:
        engine = create_engine(settings.database_url)
        with engine.begin() as connection:
            result = cancel_backtest(connection=connection, run_id=arguments.run_id)
    except (OSError, ValueError) as error:
        parser.error(f"Backtest cancel failed: {error}")
        return 2
    summary = {"run_id": result.run_id, "status": result.status.value}
    sys.stdout.write(f"{json.dumps(summary, sort_keys=True)}\n")
    return 0


def _show_backtest_resume(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Resume a failed or cancelled run as a new run linked to the original."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    configure_logging(settings.log_level)
    logger = get_logger("backtest")
    try:
        engine = create_engine(settings.database_url)
        with engine.begin() as connection:
            result = resume_backtest_from_config(
                config_path=arguments.config,
                code_version=arguments.code_version,
                data_cutoff=arguments.data_cutoff,
                connection=connection,
                original_run_id=arguments.resume_of,
                logger=logger,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Backtest resume failed: {error}")
        return 2
    summary = {"run_id": result.run_id, "status": result.status.value}
    sys.stdout.write(f"{json.dumps(summary, sort_keys=True)}\n")
    return 0


def _show_validation(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Dispatch the validation subcommands."""
    if arguments.validation_command == "run":
        return _show_validation_run(parser, arguments)
    if arguments.validation_command == "freeze":
        return _show_validation_command(parser, arguments, command="freeze")
    if arguments.validation_command == "tune":
        return _show_validation_command(parser, arguments, command="tune")
    if arguments.validation_command == "lock":
        return _show_validation_command(parser, arguments, command="lock")
    if arguments.validation_command == "evaluate":
        return _show_validation_command(parser, arguments, command="evaluate")
    if arguments.validation_command == "show":
        return _show_validation_show(parser, arguments)
    if arguments.validation_command == "report":
        return _show_validation_report(parser, arguments)
    parser.error(f"Unsupported validation command: {arguments.validation_command}")
    return 2


def _show_validation_show(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Render a validation run's status view (SP 3.71)."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as connection:
            result = show_validation(connection=connection, run_id=arguments.run_id)
    except (OSError, ValueError) as error:
        parser.error(f"Validation show failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result.to_dict(), sort_keys=True)}\n")
    return 0


def _show_validation_report(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Render a validation run's report as JSON, CSV or HTML (SP 3.71)."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as connection:
            output = report_validation(
                connection=connection,
                run_id=arguments.run_id,
                report_format=arguments.format,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Validation report failed: {error}")
        return 2
    sys.stdout.write(output + "\n")
    return 0


def _show_validation_command(
    parser: argparse.ArgumentParser,
    arguments: argparse.Namespace,
    *,
    command: str,
) -> int:
    """Apply one validation state-machine command and render the new status."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    try:
        engine = create_engine(settings.database_url)
        with engine.begin() as connection:
            result = run_validation_command(connection, arguments.run_id, command=command)
    except (OSError, ValueError) as error:
        parser.error(f"Validation {command} failed: {error}")
        return 2
    summary = {"run_id": result.run_id, "status": result.status.value}
    sys.stdout.write(f"{json.dumps(summary, sort_keys=True)}\n")
    return 0


def _show_validation_run(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Create a DRAFT validation run and render its id and status (SP 3.69)."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2
    try:
        engine = create_engine(settings.database_url)
        with engine.begin() as connection:
            result = run_validation_from_config(
                config_path=arguments.config,
                connection=connection,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Validation run failed: {error}")
        return 2
    summary = {"run_id": result.run_id, "status": result.status.value}
    sys.stdout.write(f"{json.dumps(summary, sort_keys=True)}\n")
    return 0


def _show_config(parser: argparse.ArgumentParser) -> int:
    """Load, log, and render the active non-secret configuration."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return 2

    configure_logging(settings.log_level)
    get_logger("cli").info(
        "configuration_loaded",
        extra={
            "market_target": settings.market_target,
            "data_provider_hk": settings.data_provider_hk,
            "data_provider_us": settings.data_provider_us,
        },
    )
    summary = {
        "market_target": settings.market_target.value,
        "data_provider_hk": settings.data_provider_hk,
        "data_provider_us": settings.data_provider_us,
        "log_level": settings.log_level.value,
    }
    sys.stdout.write(f"{json.dumps(summary, sort_keys=True)}\n")
    return 0


def _paper_engine(parser: argparse.ArgumentParser) -> tuple[Engine | None, Settings | None]:
    """Return a database engine and settings, or None on invalid configuration."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        parser.error(f"Invalid configuration: {error}")
        return None, None
    engine = create_engine(settings.database_url)
    return engine, settings


def _show_paper(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Dispatch the paper subcommands (SP 4.84-4.87)."""
    if arguments.paper_command == "init":
        return _show_paper_init(parser, arguments)
    if arguments.paper_command == "start":
        return _show_paper_start(parser, arguments)
    if arguments.paper_command == "stop":
        return _show_paper_stop(parser, arguments)
    if arguments.paper_command == "status":
        return _show_paper_status(parser, arguments)
    if arguments.paper_command == "signal":
        return _show_paper_signal(parser, arguments)
    if arguments.paper_command == "order":
        return _show_paper_order(parser, arguments)
    if arguments.paper_command == "approve":
        return _show_paper_approve(parser, arguments)
    if arguments.paper_command == "reject":
        return _show_paper_reject(parser, arguments)
    if arguments.paper_command == "reconcile":
        return _show_paper_reconcile(parser, arguments)
    if arguments.paper_command == "report":
        return _show_paper_report(parser, arguments)
    parser.error(f"Unsupported paper command: {arguments.paper_command}")
    return 2


def _show_paper_init(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Create a DRAFT paper run (SP 4.84)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        with engine.begin() as connection:
            result = paper_init_command(
                config_path=arguments.config,
                store=PaperRepositoryStore(connection),
                code_version=arguments.code_version,
                dataset_fingerprint=arguments.dataset_fingerprint,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Paper init failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result.to_dict(), sort_keys=True)}\n")
    return 0


def _show_paper_start(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Approve and activate a paper run (SP 4.84)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        with engine.begin() as connection:
            result = paper_start_command(
                store=PaperRepositoryStore(connection),
                run_id=arguments.run_id,
                approver=arguments.approver,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Paper start failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result.to_dict(), sort_keys=True)}\n")
    return 0


def _show_paper_stop(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Stop a paper run (SP 4.84)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        with engine.begin() as connection:
            result = paper_stop_command(
                store=PaperRepositoryStore(connection), run_id=arguments.run_id
            )
    except (OSError, ValueError) as error:
        parser.error(f"Paper stop failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result.to_dict(), sort_keys=True)}\n")
    return 0


def _show_paper_status(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Show a paper run's status view (SP 4.84)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        with engine.connect() as connection:
            result = paper_status_command(
                store=PaperRepositoryStore(connection), run_id=arguments.run_id
            )
    except (OSError, ValueError) as error:
        parser.error(f"Paper status failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result, sort_keys=True)}\n")
    return 0


def _show_paper_signal(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Derive and persist paper orders from target weights (SP 4.85)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        targets = _parse_key_value(arguments.target, float, "target")
        prices = _parse_key_value(arguments.price, float, "price")
    except ValueError as error:
        parser.error(str(error))
        return 2
    try:
        with engine.begin() as connection:
            result = paper_signal_command(
                store=PaperRepositoryStore(connection),
                run_id=arguments.run_id,
                rebalance_date=arguments.rebalance_date,
                targets=targets,
                prices=prices,
                source_run_id=arguments.source_run_id,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Paper signal failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result.to_dict(), sort_keys=True)}\n")
    return 0


def _parse_key_value(
    items: Sequence[str], convert: Callable[[str], float], what: str
) -> dict[str, float]:
    """Parse ``KEY:VALUE`` CLI arguments into a mapping."""
    parsed: dict[str, float] = {}
    for item in items:
        if ":" not in item:
            raise ValueError(f"Invalid {what} {item!r}; expected KEY:VALUE.")
        key, raw = item.split(":", 1)
        try:
            parsed[key] = convert(raw)
        except ValueError as error:
            raise ValueError(f"Invalid {what} value for {key!r}: {error}") from error
    return parsed


def _show_paper_order(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Inspect a paper run's orders (SP 4.85)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        with engine.connect() as connection:
            store = PaperRepositoryStore(connection)
            if arguments.order_command == "list":
                payload: object = paper_order_list_command(store=store, run_id=arguments.run_id)
            else:
                payload = paper_order_show_command(
                    store=store, run_id=arguments.run_id, order_id=arguments.order_id
                )
    except (OSError, ValueError) as error:
        parser.error(f"Paper order {arguments.order_command} failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(payload, sort_keys=True)}\n")
    return 0


def _show_paper_approve(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Record an approval for a paper order or run (SP 4.86)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        with engine.begin() as connection:
            result = paper_approve_order_command(
                store=PaperRepositoryStore(connection),
                run_id=arguments.run_id,
                order_id=arguments.order_id or "run",
                approver=arguments.approver,
                decision=ApprovalDecision.APPROVED,
                reason=arguments.reason,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Paper approve failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result.to_dict(), sort_keys=True)}\n")
    return 0


def _show_paper_reject(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Record a rejection for a paper order (SP 4.86)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        with engine.begin() as connection:
            result = paper_approve_order_command(
                store=PaperRepositoryStore(connection),
                run_id=arguments.run_id,
                order_id=arguments.order_id,
                approver=arguments.approver,
                decision=ApprovalDecision.REJECTED,
                reason=arguments.reason,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Paper reject failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result.to_dict(), sort_keys=True)}\n")
    return 0


def _show_paper_reconcile(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Reconcile a paper run's account against its net value (SP 4.87)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        with engine.begin() as connection:
            result = paper_reconcile_command(
                store=PaperRepositoryStore(connection),
                run_id=arguments.run_id,
                as_of=arguments.as_of,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Paper reconcile failed: {error}")
        return 2
    sys.stdout.write(f"{json.dumps(result.to_dict(), sort_keys=True)}\n")
    return 0


def _show_paper_report(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    """Render a paper run's report as JSON, CSV or HTML (SP 4.87)."""
    engine, _settings = _paper_engine(parser)
    if engine is None:
        return 2
    try:
        with engine.connect() as connection:
            output = paper_report_command(
                store=PaperRepositoryStore(connection),
                run_id=arguments.run_id,
                report_format=arguments.format,
            )
    except (OSError, ValueError) as error:
        parser.error(f"Paper report failed: {error}")
        return 2
    sys.stdout.write(output + "\n")
    return 0
