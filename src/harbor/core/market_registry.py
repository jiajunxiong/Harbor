"""Market configuration registry for Harbor.

Each market carries its own data-source configuration, stock-pool source, and
corporate-action rule mapping so that Hong Kong and United States rules are
never mixed. The registry intentionally mirrors the ``action_type`` vocabulary
used by the ``corporate_actions`` table.
"""

from dataclasses import dataclass
from enum import StrEnum

from harbor.config import MarketTarget


class CorporateActionType(StrEnum):
    """Corporate action types supported across both markets."""

    SPLIT = "split"
    CONSOLIDATION = "consolidation"
    RIGHTS_ISSUE = "rights_issue"
    MERGER = "merger"
    SPIN_OFF = "spin_off"
    TENDER_OFFER = "tender_offer"
    DIVIDEND = "dividend"


@dataclass(frozen=True)
class MarketConfig:
    """Static configuration for a single market."""

    market: MarketTarget
    default_provider: str
    allowed_providers: tuple[str, ...]
    currency: str
    stock_pool_source: str
    corporate_action_types: frozenset[CorporateActionType]


HK_CONFIG = MarketConfig(
    market=MarketTarget.HK,
    default_provider="yfinance",
    allowed_providers=("yfinance", "akshare", "mock"),
    currency="HKD",
    stock_pool_source="hkex_universe",
    corporate_action_types=frozenset(
        {
            CorporateActionType.RIGHTS_ISSUE,
            CorporateActionType.CONSOLIDATION,
            CorporateActionType.TENDER_OFFER,
            CorporateActionType.DIVIDEND,
        }
    ),
)

US_CONFIG = MarketConfig(
    market=MarketTarget.US,
    default_provider="yfinance",
    allowed_providers=("yfinance", "mock"),
    currency="USD",
    stock_pool_source="sp500_constituents",
    corporate_action_types=frozenset(
        {
            CorporateActionType.SPLIT,
            CorporateActionType.MERGER,
            CorporateActionType.SPIN_OFF,
            CorporateActionType.DIVIDEND,
        }
    ),
)

MARKET_CONFIGS: dict[MarketTarget, MarketConfig] = {
    MarketTarget.HK: HK_CONFIG,
    MarketTarget.US: US_CONFIG,
}


def get_market_config(market: MarketTarget) -> MarketConfig:
    """Return the static configuration for a market."""
    return MARKET_CONFIGS[market]


def validate_provider(market: MarketTarget, provider: str) -> str:
    """Return the provider if it is allowed for the market, otherwise raise."""
    config = get_market_config(market)
    if provider not in config.allowed_providers:
        raise ValueError(f"Provider {provider!r} is not supported for {market.value}.")
    return provider
