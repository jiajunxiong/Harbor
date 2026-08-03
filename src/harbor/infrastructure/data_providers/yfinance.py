"""yfinance-backed market data providers for Harbor."""

import importlib
from typing import Any, cast

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability, MarketDataProvider, ProviderCapabilities

_ALL_CAPABILITIES = frozenset(Capability)


class YFinanceProvider(MarketDataProvider):
    """Base class for yfinance-backed providers.

    The ``yfinance`` package is imported lazily so that importing this module
    does not require the package to be installed.
    """

    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize a raw symbol to the yfinance ticker form."""
        raise NotImplementedError

    def _ticker(self, symbol: str) -> Any:
        """Return a yfinance Ticker for a market-normalized symbol."""
        yfinance = cast(Any, importlib.import_module("yfinance"))
        return yfinance.Ticker(self._normalize_symbol(symbol))


class HKYFinanceProvider(YFinanceProvider):
    """yfinance-backed provider for the Hong Kong market."""

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities the provider offers for Hong Kong."""
        return ProviderCapabilities({MarketTarget.HK: _ALL_CAPABILITIES})

    def _normalize_symbol(self, symbol: str) -> str:
        """Return a 4-digit HK code with the ``.HK`` suffix."""
        return f"{symbol.removesuffix('.HK')}.HK"


class USYFinanceProvider(YFinanceProvider):
    """yfinance-backed provider for the United States market."""

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities the provider offers for the United States."""
        return ProviderCapabilities({MarketTarget.US: _ALL_CAPABILITIES})

    def _normalize_symbol(self, symbol: str) -> str:
        """Return the ticker as given, trimmed and uppercased."""
        return symbol.strip().upper()
