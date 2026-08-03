"""Provider capability declarations for Harbor.

A data provider may support different capabilities for different markets.
This module defines the capability vocabulary, the market-dimensioned
capability declaration, and the unified provider interface.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from harbor.config import MarketTarget


class Capability(StrEnum):
    """A data capability that a provider may offer for a market."""

    DAILY_QUOTES = "daily_quotes"
    DIVIDENDS = "dividends"
    FUNDAMENTALS = "fundamentals"
    CORPORATE_ACTIONS = "corporate_actions"
    ADJUSTED_FACTORS = "adjusted_factors"


@dataclass(frozen=True)
class ProviderCapabilities:
    """The capabilities a provider offers, declared per market."""

    by_market: Mapping[MarketTarget, frozenset[Capability]]

    def supports(self, market: MarketTarget, capability: Capability) -> bool:
        """Return whether the provider offers the capability for a market."""
        return capability in self.by_market.get(market, frozenset())

    def markets(self) -> tuple[MarketTarget, ...]:
        """Return the markets for which the provider declares capabilities."""
        return tuple(self.by_market)


class MarketDataProvider(ABC):
    """A data source for one or more markets.

    Providers declare the markets and data capabilities they support through
    :meth:`capabilities`. Callers should gate data-fetch calls on
    :meth:`ProviderCapabilities.supports`; methods for unsupported capabilities
    raise :class:`NotImplementedError`. Every data method accepts a ``market``
    argument so that Hong Kong and United States data are never mixed within a
    single call. Returned rows are plain mappings matching the shapes expected
    by the storage repository.
    """

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Return the markets and data capabilities this provider offers."""
        raise NotImplementedError

    def list_securities(self, market: MarketTarget) -> Sequence[Mapping[str, Any]]:
        """Return the securities universe for a market."""
        raise NotImplementedError(f"{type(self).__name__} does not support list_securities")

    def fetch_daily_quotes(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Return daily OHLCV rows for a symbol within a date range."""
        raise NotImplementedError(f"{type(self).__name__} does not support fetch_daily_quotes")

    def fetch_dividends(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Return dividend rows for a symbol within a date range."""
        raise NotImplementedError(f"{type(self).__name__} does not support fetch_dividends")

    def fetch_financials(
        self,
        market: MarketTarget,
        symbol: str,
    ) -> Sequence[Mapping[str, Any]]:
        """Return reported financial indicator rows for a symbol."""
        raise NotImplementedError(f"{type(self).__name__} does not support fetch_financials")

    def fetch_fundamentals(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Return fundamental metric rows for a symbol within a date range."""
        raise NotImplementedError(f"{type(self).__name__} does not support fetch_fundamentals")

    def fetch_corporate_actions(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Return corporate action rows for a symbol within a date range."""
        raise NotImplementedError(f"{type(self).__name__} does not support fetch_corporate_actions")

    def fetch_adjusted_factors(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Return adjustment factor rows for a symbol within a date range."""
        raise NotImplementedError(f"{type(self).__name__} does not support fetch_adjusted_factors")
