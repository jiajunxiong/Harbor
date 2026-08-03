"""Mock market data provider for Harbor."""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability, MarketDataProvider, ProviderCapabilities

_ALL_CAPABILITIES = frozenset(Capability)

_HK_SECURITIES: tuple[tuple[str, str, str], ...] = (
    ("0001.HK", "CK Hutchison Holdings", "HKEX"),
    ("0002.HK", "CLP Holdings", "HKEX"),
    ("0003.HK", "Hong Kong and China Gas", "HKEX"),
    ("0005.HK", "HSBC Holdings", "HKEX"),
    ("0011.HK", "Hang Seng Bank", "HKEX"),
    ("0016.HK", "Sun Hung Kai Properties", "HKEX"),
    ("0017.HK", "New World Development", "HKEX"),
    ("0027.HK", "Galaxy Entertainment", "HKEX"),
    ("0066.HK", "MTR Corporation", "HKEX"),
    ("0123.HK", "Yuexiu Property", "HKEX"),
    ("0388.HK", "Hong Kong Exchanges and Clearing", "HKEX"),
    ("0700.HK", "Tencent Holdings", "HKEX"),
    ("0883.HK", "CNOOC Limited", "HKEX"),
    ("0939.HK", "China Construction Bank", "HKEX"),
    ("0941.HK", "China Mobile", "HKEX"),
    ("0998.HK", "China Telecom", "HKEX"),
)

_US_SECURITIES: tuple[tuple[str, str, str], ...] = (
    ("AAPL", "Apple Inc.", "NASDAQ"),
    ("MSFT", "Microsoft Corp.", "NASDAQ"),
    ("GOOGL", "Alphabet Inc.", "NASDAQ"),
    ("AMZN", "Amazon.com Inc.", "NASDAQ"),
    ("META", "Meta Platforms Inc.", "NASDAQ"),
    ("TSLA", "Tesla Inc.", "NASDAQ"),
    ("NVDA", "NVIDIA Corp.", "NASDAQ"),
    ("JPM", "JPMorgan Chase & Co.", "NYSE"),
    ("V", "Visa Inc.", "NYSE"),
    ("JNJ", "Johnson & Johnson", "NYSE"),
    ("WMT", "Walmart Inc.", "NYSE"),
    ("PG", "Procter & Gamble Co.", "NYSE"),
    ("DIS", "The Walt Disney Co.", "NYSE"),
    ("NFLX", "Netflix Inc.", "NASDAQ"),
    ("INTC", "Intel Corp.", "NASDAQ"),
    ("CSCO", "Cisco Systems Inc.", "NASDAQ"),
)

_SECURITIES_BY_MARKET: dict[MarketTarget, tuple[tuple[str, str, str], ...]] = {
    MarketTarget.HK: _HK_SECURITIES,
    MarketTarget.US: _US_SECURITIES,
}


class MockProvider(MarketDataProvider):
    """A deterministic mock provider used for prototyping without a network."""

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities the mock provider offers for both markets."""
        return ProviderCapabilities(
            {
                MarketTarget.HK: _ALL_CAPABILITIES,
                MarketTarget.US: _ALL_CAPABILITIES,
            }
        )

    def list_securities(self, market: MarketTarget) -> Sequence[Mapping[str, Any]]:
        """Return a fixed mock securities universe for a market."""
        rows = _SECURITIES_BY_MARKET.get(market)
        if rows is None:
            raise ValueError(f"list_securities does not support {market.value!r}.")
        return [
            {
                "market": market.value,
                "symbol": symbol,
                "name": name,
                "exchange": exchange,
                "list_date": date(2000, 1, 3),
                "delist_date": None,
                "is_active": True,
            }
            for symbol, name, exchange in rows
        ]
