"""Mock market data provider for Harbor."""

import random
import zlib
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability, MarketDataProvider, ProviderCapabilities
from harbor.core.market_registry import get_market_config

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

_CURRENCY_BY_MARKET: dict[MarketTarget, str] = {
    MarketTarget.HK: "HKD",
    MarketTarget.US: "USD",
}

_DIVIDEND_INTERVAL_DAYS: dict[MarketTarget, int] = {
    MarketTarget.HK: 180,
    MarketTarget.US: 90,
}

_EQUITY_RANGE_BY_MARKET: dict[MarketTarget, tuple[float, float]] = {
    MarketTarget.HK: (20_000_000_000.0, 500_000_000_000.0),
    MarketTarget.US: (20_000_000_000.0, 400_000_000_000.0),
}

_FINANCIALS_START_YEAR = 2020
_FINANCIALS_END_YEAR = 2025


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

    def fetch_dividends(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Return deterministic mock dividend rows for a symbol."""
        if market not in _SECURITIES_BY_MARKET:
            raise ValueError(f"fetch_dividends does not support {market.value!r}.")
        if end < start:
            raise ValueError("end must not be earlier than start.")
        currency = _CURRENCY_BY_MARKET[market]
        interval_days = _DIVIDEND_INTERVAL_DAYS[market]
        rng = random.Random(_symbol_seed(market, symbol))
        ex_date = start + timedelta(days=rng.randint(0, interval_days - 1))
        rows: list[Mapping[str, Any]] = []
        index = 0
        while ex_date <= end:
            index += 1
            is_special = index % 5 == 0
            amount = round(rng.uniform(1.0, 10.0) if is_special else rng.uniform(0.1, 5.0), 2)
            rows.append(
                {
                    "market": market.value,
                    "symbol": symbol,
                    "ex_date": ex_date,
                    "record_date": ex_date + timedelta(days=2),
                    "payment_date": ex_date + timedelta(days=12),
                    "amount": amount,
                    "type": "special" if is_special else "regular",
                    "currency": currency,
                }
            )
            ex_date += timedelta(days=interval_days)
        return rows

    def fetch_financials(
        self,
        market: MarketTarget,
        symbol: str,
    ) -> Sequence[Mapping[str, Any]]:
        """Return deterministic mock financial indicator rows for a symbol."""
        if market not in _SECURITIES_BY_MARKET:
            raise ValueError(f"fetch_financials does not support {market.value!r}.")
        rng = random.Random(_symbol_seed(market, symbol))
        equity_low, equity_high = _EQUITY_RANGE_BY_MARKET[market]
        rows: list[Mapping[str, Any]] = []
        for year in range(_FINANCIALS_START_YEAR, _FINANCIALS_END_YEAR + 1):
            total_equity = round(rng.uniform(equity_low, equity_high), 2)
            roe = round(rng.uniform(0.05, 0.35), 4)
            net_income = round(total_equity * roe, 2)
            net_margin = rng.uniform(0.05, 0.30)
            revenue = round(net_income / net_margin, 2)
            rows.append(
                {
                    "market": market.value,
                    "symbol": symbol,
                    "report_date": date(year, 12, 31),
                    "fiscal_period": str(year),
                    "roe": roe,
                    "net_income": net_income,
                    "total_equity": total_equity,
                    "revenue": revenue,
                }
            )
        return rows

    def fetch_corporate_actions(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Return deterministic mock corporate action rows for a symbol."""
        if market not in _SECURITIES_BY_MARKET:
            raise ValueError(f"fetch_corporate_actions does not support {market.value!r}.")
        if end < start:
            raise ValueError("end must not be earlier than start.")
        action_types = tuple(
            sorted(action.value for action in get_market_config(market).corporate_action_types)
        )
        rng = random.Random(_symbol_seed(market, symbol))
        ex_date = start + timedelta(days=rng.randint(0, 364))
        rows: list[Mapping[str, Any]] = []
        index = 0
        while ex_date <= end:
            index += 1
            rows.append(
                {
                    "market": market.value,
                    "symbol": symbol,
                    "action_id": f"{symbol}-{index}",
                    "announce_date": ex_date - timedelta(days=14),
                    "ex_date": ex_date,
                    "record_date": ex_date + timedelta(days=2),
                    "effective_date": ex_date + timedelta(days=10),
                    "action_type": action_types[(index - 1) % len(action_types)],
                    "status": "completed",
                    "source": "mock",
                }
            )
            ex_date += timedelta(days=365)
        return rows
