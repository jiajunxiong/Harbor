"""Mock market data provider for Harbor."""

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability, MarketDataProvider, ProviderCapabilities

_ALL_CAPABILITIES = frozenset(Capability)


class MockProvider(MarketDataProvider):
    """A deterministic mock provider used for prototyping without a network.

    The capability declaration below reflects the intended full coverage for
    both markets. Individual data-generation methods are implemented
    incrementally by the corresponding MVP 1 stories (1.38-1.47); until then
    they inherit the base interface's ``NotImplementedError`` behavior.
    """

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities the mock provider offers for both markets."""
        return ProviderCapabilities(
            {
                MarketTarget.HK: _ALL_CAPABILITIES,
                MarketTarget.US: _ALL_CAPABILITIES,
            }
        )
