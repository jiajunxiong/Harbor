"""Provider capability declarations for Harbor.

A data provider may support different capabilities for different markets.
This module defines the capability vocabulary and a market-dimensioned
declaration object used by provider implementations.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

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
