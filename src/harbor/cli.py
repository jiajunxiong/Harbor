"""Command-line entry points for Harbor."""

import argparse
import json
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from harbor import __version__
from harbor.config import Settings
from harbor.infrastructure.data_providers.factory import print_capability_report
from harbor.logging import configure_logging, get_logger


def build_parser() -> argparse.ArgumentParser:
    """Build the Harbor command-line parser."""
    parser = argparse.ArgumentParser(prog="harbor-cli", description="Harbor market-data tools")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("config", help="Show the active non-secret configuration.")
    subparsers.add_parser("providers", help="Show the data provider capability report.")
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
    parser.error(f"Unsupported command: {arguments.command}")
    return 2


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
