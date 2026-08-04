"""Command-line entry points for Harbor."""

import argparse
import json
import sys
import uuid
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy import create_engine

from harbor import __version__
from harbor.config import MarketTarget, Settings
from harbor.core.ingestion import SecuritiesIngestor
from harbor.infrastructure.data_providers.factory import (
    create_provider,
    print_capability_report,
)
from harbor.logging import configure_logging, get_logger
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
    if arguments.fetch_command == "securities":
        try:
            summary = _fetch_securities(arguments.market, settings)
        except (NotImplementedError, ValueError) as error:
            parser.error(f"Fetch failed: {error}")
            return 2
        sys.stdout.write(f"{json.dumps(summary, sort_keys=True)}\n")
        return 0
    parser.error(f"Unsupported fetch command: {arguments.fetch_command}")
    return 2


def _fetch_securities(market: MarketTarget, settings: Settings) -> dict[str, object]:
    """Fetch and store the securities universe for a market."""
    provider_name = (
        settings.data_provider_hk if market is MarketTarget.HK else settings.data_provider_us
    )
    provider = create_provider(market, provider_name)
    run_id = uuid.uuid4().hex
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        repository = Repository(connection)
        ingestor = SecuritiesIngestor(repository, run_id=run_id)
        count = ingestor.ingest(provider, market)
    return {"market": market.value, "provider": provider_name, "count": count}


def _show_providers() -> int:
    """Print the registered data provider capability report."""
    print_capability_report()
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
