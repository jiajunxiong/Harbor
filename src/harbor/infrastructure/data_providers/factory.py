"""Provider factory for Harbor.

The factory resolves a market's configured provider name into a concrete
:class:`~harbor.core.interfaces.MarketDataProvider`. Each market resolves
independently, so ``DATA_PROVIDER_HK`` and ``DATA_PROVIDER_US`` may differ.
"""

import sys
from typing import TextIO, TypedDict

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability, MarketDataProvider
from harbor.core.market_registry import validate_provider
from harbor.infrastructure.data_providers.akshare import HKAKShareProvider
from harbor.infrastructure.data_providers.mock import MockProvider
from harbor.infrastructure.data_providers.yfinance import (
    HKYFinanceProvider,
    USYFinanceProvider,
)

_PROVIDER_CLASSES: dict[tuple[MarketTarget, str], type[MarketDataProvider]] = {
    (MarketTarget.HK, "mock"): MockProvider,
    (MarketTarget.US, "mock"): MockProvider,
    (MarketTarget.HK, "yfinance"): HKYFinanceProvider,
    (MarketTarget.US, "yfinance"): USYFinanceProvider,
    (MarketTarget.HK, "akshare"): HKAKShareProvider,
}


class ProviderReportEntry(TypedDict):
    """A single provider's capability report entry."""

    provider: str
    markets: list[str]
    capabilities: dict[str, list[str]]


def capability_report() -> list[ProviderReportEntry]:
    """Return a per-provider capability report aggregated by provider name."""
    by_provider: dict[str, dict[MarketTarget, frozenset[Capability]]] = {}
    for (_, provider_name), provider_class in _PROVIDER_CLASSES.items():
        for market, capabilities in provider_class().capabilities().by_market.items():
            existing = by_provider.setdefault(provider_name, {})
            existing[market] = existing.get(market, frozenset()) | capabilities
    report: list[ProviderReportEntry] = []
    for provider_name in sorted(by_provider):
        capabilities_by_market = by_provider[provider_name]
        report.append(
            {
                "provider": provider_name,
                "markets": [
                    market.value
                    for market in sorted(capabilities_by_market, key=lambda market: market.value)
                ],
                "capabilities": {
                    market.value: sorted(
                        capability.value for capability in capabilities_by_market[market]
                    )
                    for market in sorted(capabilities_by_market, key=lambda market: market.value)
                },
            }
        )
    return report


def print_capability_report(stream: TextIO | None = None) -> None:
    """Print a human-readable capability report for all registered providers."""
    output = stream if stream is not None else sys.stdout
    for entry in capability_report():
        markets = ", ".join(entry["markets"])
        capabilities = "; ".join(
            f"{market}: {', '.join(caps)}" for market, caps in entry["capabilities"].items()
        )
        output.write(
            f"provider={entry['provider']} markets=[{markets}] capabilities={{{capabilities}}}\n"
        )


def create_provider(market: MarketTarget, provider_name: str) -> MarketDataProvider:
    """Build the provider configured for a single market.

    Args:
        market: The market the provider will serve.
        provider_name: The provider identifier from ``DATA_PROVIDER_HK`` or
            ``DATA_PROVIDER_US``.

    Returns:
        A configured provider instance.

    Raises:
        ValueError: If the provider name is not allowed for the market.
        NotImplementedError: If the provider name is allowed but not yet
            implemented.
    """
    validate_provider(market, provider_name)
    provider_class = _PROVIDER_CLASSES.get((market, provider_name))
    if provider_class is None:
        raise NotImplementedError(f"Provider {provider_name!r} is not implemented yet.")
    return provider_class()
