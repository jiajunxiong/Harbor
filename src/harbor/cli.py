"""Command-line entry points for Harbor."""

import argparse
import json
import sys
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import create_engine

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
from harbor.core.quality_report import render_quality_csv, summarize_quality_issues
from harbor.infrastructure.data_providers.factory import (
    create_provider,
    print_capability_report,
)
from harbor.logging import configure_logging, get_logger
from harbor.services.backtest import run_backtest_from_config, show_backtest
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
    daily_parser = fetch_subparsers.add_parser("daily", help="Fetch daily quotes for a symbol.")
    daily_parser.add_argument(
        "--market", type=MarketTarget, required=True, help="Market to fetch (HK or US)."
    )
    daily_parser.add_argument("--symbol", required=True, help="Security symbol.")
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
        try:
            if arguments.fetch_command == "securities":
                summary = _fetch_securities(arguments.market, settings)
            elif arguments.fetch_command == "daily":
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
    try:
        engine = create_engine(settings.database_url)
        with engine.begin() as connection:
            result = run_backtest_from_config(
                config_path=arguments.config,
                code_version=arguments.code_version,
                data_cutoff=arguments.data_cutoff,
                connection=connection,
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
