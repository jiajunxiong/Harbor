"""Mock market data provider for Harbor."""

import random
import zlib
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
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

_PRICE_RANGE_BY_MARKET: dict[MarketTarget, tuple[float, float]] = {
    MarketTarget.HK: (20.0, 200.0),
    MarketTarget.US: (20.0, 500.0),
}


def _symbol_seed(market: MarketTarget, symbol: str) -> int:
    """Return a deterministic seed derived from a market and symbol."""
    return zlib.crc32(f"{market.value}:{symbol}".encode("utf-8"))


def _trading_days(start: date, end: date) -> Iterable[date]:
    """Yield weekdays within a closed date range."""
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


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

    def fetch_daily_quotes(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Return deterministic mock daily OHLCV rows for a symbol."""
        if market not in _SECURITIES_BY_MARKET:
            raise ValueError(f"fetch_daily_quotes does not support {market.value!r}.")
        if end < start:
            raise ValueError("end must not be earlier than start.")
        rng = random.Random(_symbol_seed(market, symbol))
        price_low, price_high = _PRICE_RANGE_BY_MARKET[market]
        current_price = round(rng.uniform(price_low, price_high), 2)
        rows: list[Mapping[str, Any]] = []
        for day in _trading_days(start, end):
            open_price = current_price
            close_price = round(max(1.0, open_price * (1 + rng.uniform(-0.03, 0.03))), 2)
            high_price = round(max(open_price, close_price) * (1 + rng.uniform(0.0, 0.02)), 2)
            low_price = round(min(open_price, close_price) * (1 - rng.uniform(0.0, 0.02)), 2)
            rows.append(
                {
                    "market": market.value,
                    "symbol": symbol,
                    "date": day,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": rng.randint(1_000_000, 50_000_000),
                    "adjusted_close": close_price,
                    "source": "mock",
                }
            )
            current_price = close_price
        return rows
