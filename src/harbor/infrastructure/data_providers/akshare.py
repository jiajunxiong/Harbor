"""AkShare-backed market data provider for Harbor."""

import importlib
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability, MarketDataProvider, ProviderCapabilities
from harbor.infrastructure.data_providers.yfinance import (
    standardize_daily_quotes,
    standardize_dividends,
    standardize_splits,
)

_ALL_CAPABILITIES = frozenset(Capability)

_DAILY_COLUMN_MAP = {
    "Open": "开盘",
    "High": "最高",
    "Low": "最低",
    "Close": "收盘",
    "Volume": "成交量",
}


def standardize_ak_daily_quotes(
    market: MarketTarget,
    symbol: str,
    dates: Sequence[date | datetime],
    columns: Mapping[str, Sequence[object]],
    source: str,
) -> list[dict[str, Any]]:
    """Normalize AkShare Hong Kong daily columns into daily quote rows."""
    renamed = {
        english_name: columns.get(ak_name, ())
        for english_name, ak_name in _DAILY_COLUMN_MAP.items()
    }
    return standardize_daily_quotes(market, symbol, dates, renamed, source)


def standardize_ak_dividends(
    market: MarketTarget,
    symbol: str,
    dividends: Mapping[object, object],
    source: str,
) -> list[dict[str, Any]]:
    """Normalize AkShare Hong Kong dividend payouts into dividend rows."""
    return standardize_dividends(market, symbol, dividends, source)


def standardize_ak_corporate_actions(
    market: MarketTarget,
    symbol: str,
    actions: Mapping[object, object],
    source: str,
) -> list[dict[str, Any]]:
    """Normalize AkShare Hong Kong corporate action factors into rows."""
    return standardize_splits(market, symbol, actions, source)


class HKAKShareProvider(MarketDataProvider):
    """AkShare-backed provider for the Hong Kong market."""

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities the provider offers for Hong Kong."""
        return ProviderCapabilities({MarketTarget.HK: _ALL_CAPABILITIES})

    def _ak(self) -> Any:
        """Return the akshare module, imported lazily."""
        return cast(Any, importlib.import_module("akshare"))

    def _normalize_symbol(self, symbol: str) -> str:
        """Return a 5-digit AkShare Hong Kong stock code."""
        return symbol.removesuffix(".HK").zfill(5)

    def _require_market(self, market: MarketTarget) -> None:
        """Reject a market the provider does not serve."""
        if market not in self.capabilities().markets():
            raise ValueError(f"{type(self).__name__} does not support {market.value!r}.")

    def fetch_daily_quotes(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Fetch and standardize daily quotes for a symbol from AkShare."""
        self._require_market(market)
        if end < start:
            raise ValueError("end must not be earlier than start.")
        frame = self._ak().stock_hk_hist(
            symbol=self._normalize_symbol(symbol),
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            adjust="",
        )
        return standardize_ak_daily_quotes(
            market,
            symbol,
            list(frame.index),
            dict(frame.to_dict("list")),
            source="akshare",
        )
