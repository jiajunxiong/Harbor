"""Provider factory for Harbor.

The factory resolves a market's configured provider name into a concrete
:class:`~harbor.core.interfaces.MarketDataProvider`. Each market resolves
independently, so ``DATA_PROVIDER_HK`` and ``DATA_PROVIDER_US`` may differ.
"""

from harbor.config import MarketTarget
from harbor.core.interfaces import MarketDataProvider
from harbor.core.market_registry import validate_provider
from harbor.infrastructure.data_providers.mock import MockProvider

_PROVIDER_CLASSES: dict[str, type[MarketDataProvider]] = {
    "mock": MockProvider,
}


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
    provider_class = _PROVIDER_CLASSES.get(provider_name)
    if provider_class is None:
        raise NotImplementedError(f"Provider {provider_name!r} is not implemented yet.")
    return provider_class()
